"""Deterministically verify the two offline mathematics knowledge-graph pages."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import html
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


_PAYLOAD = re.compile(
    r'<script\s+type=["\']application/json["\']\s+id=["\']graph-data["\']\s*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"href", "src"} and value:
                self.references.append((name.lower(), value.strip()))


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    error_codes: tuple[str, ...]
    node_count: int
    cross_link_count: int
    important_cross_link_count: int
    confirmed_count: int
    unmapped_count: int
    cross_subject_count: int
    evidence_node_count: int
    broken_links: tuple[str, ...]
    external_assets: tuple[str, ...]
    quarantined_labels: tuple[str, ...]


def _extract_payload(page: Path, errors: list[str]) -> dict[str, Any] | None:
    text = page.read_text(encoding="utf-8")
    match = _PAYLOAD.search(text)
    if not match:
        errors.append("missing_payload")
        return None
    try:
        value = json.loads(html.unescape(match.group(1)))
    except (json.JSONDecodeError, TypeError):
        errors.append("invalid_payload")
        return None
    if not isinstance(value, dict):
        errors.append("invalid_payload")
        return None
    return value


def _inspect_links(
    root: Path,
    page: Path,
    *,
    broken: list[str],
    external: list[str],
    errors: list[str],
) -> None:
    text = page.read_text(encoding="utf-8")
    parser = _LinkCollector()
    parser.feed(text)
    root_resolved = root.resolve()
    for attribute, raw in parser.references:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
        if scheme in {"http", "https"}:
            external.append(f"{page.name}:{attribute}={raw}")
            continue
        if scheme or raw.startswith("//"):
            errors.append("unsafe_scheme")
            external.append(f"{page.name}:{attribute}={raw}")
            continue
        path_text = unquote(parsed.path)
        if not path_text:
            continue
        candidate = (page.parent / path_text).resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            broken.append(f"{page.name}:{raw}")
            continue
        if not candidate.exists():
            broken.append(f"{page.name}:{raw}")
    if external:
        errors.append("external_asset")
    if re.search(
        r"(?:@import\s+(?:url\()?\s*[\"']?https?://|"
        r"url\(\s*[\"']?https?://|(?:fetch|WebSocket)\s*\(\s*[\"']https?://)",
        text,
        re.IGNORECASE,
    ):
        errors.append("external_asset")
        external.append(f"{page.name}:embedded_remote_reference")
    if broken:
        errors.append("broken_link")


def _evidence_count(node: Any) -> int:
    if not isinstance(node, dict):
        return 0
    mastery = node.get("mastery")
    if not isinstance(mastery, dict):
        return 0
    value = mastery.get("evidence_count", 0)
    return value if type(value) is int and value >= 0 else 0


def verify_graph_pages(root: Path, pages: Iterable[Path]) -> VerificationResult:
    """Verify shared payload, offline links, graph richness and evidence isolation."""
    root = Path(root)
    page_list = tuple(Path(page) for page in pages)
    errors: list[str] = []
    broken: list[str] = []
    external: list[str] = []
    payloads: list[dict[str, Any]] = []
    if len(page_list) != 2:
        errors.append("page_count")

    for page in page_list:
        if not page.is_file():
            errors.append("missing_page")
            continue
        payload = _extract_payload(page, errors)
        if payload is not None:
            payloads.append(payload)
        _inspect_links(root, page, broken=broken, external=external, errors=errors)

    if len(payloads) == 2 and payloads[0] != payloads[1]:
        errors.append("payload_mismatch")
    payload = payloads[0] if payloads else {}
    nodes = payload.get("nodes", {})
    edges = payload.get("edges", [])
    audit = payload.get("audit", [])
    nodes = nodes if isinstance(nodes, dict) else {}
    edges = edges if isinstance(edges, list) else []
    audit = audit if isinstance(audit, list) else []

    important = sum(
        1 for edge in edges
        if isinstance(edge, dict) and edge.get("importance") == 3
    )
    if important < 1:
        errors.append("missing_important_cross_link")

    counts = {"confirmed": 0, "unmapped": 0, "cross_subject": 0}
    quarantined: list[str] = []
    confirmed_evidence = 0
    for item in audit:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status in counts:
            counts[status] += 1
        if status == "confirmed":
            value = item.get("evidence_count", 0)
            if type(value) is int and value >= 0:
                confirmed_evidence += value
        elif status in {"unmapped", "cross_subject"}:
            label = item.get("label")
            if isinstance(label, str) and label.strip():
                quarantined.append(label.strip())

    node_evidence = sum(_evidence_count(node) for node in nodes.values())
    if node_evidence != confirmed_evidence:
        errors.append("evidence_leakage")

    return VerificationResult(
        ok=not errors,
        error_codes=tuple(sorted(set(errors))),
        node_count=len(nodes),
        cross_link_count=len(edges),
        important_cross_link_count=important,
        confirmed_count=counts["confirmed"],
        unmapped_count=counts["unmapped"],
        cross_subject_count=counts["cross_subject"],
        evidence_node_count=sum(1 for node in nodes.values() if _evidence_count(node) > 0),
        broken_links=tuple(sorted(set(broken))),
        external_assets=tuple(sorted(set(external))),
        quarantined_labels=tuple(sorted(set(quarantined))),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="验证晴子数学双视图知识图谱")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--panorama", type=Path, required=True)
    parser.add_argument("--explorer", type=Path, required=True)
    args = parser.parse_args()
    result = verify_graph_pages(args.root, (args.panorama, args.explorer))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
