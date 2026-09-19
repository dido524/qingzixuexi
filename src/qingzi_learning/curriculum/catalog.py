"""Load and validate a photo-grounded, extensible course catalog."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any


_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NODE_TYPES = {"unit", "practice", "math_play", "review"}
_EDGE_TYPES = {"related", "application", "prerequisite"}


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Read the default bundled book or a caller-supplied local catalog."""
    if path is None:
        resource = files("qingzi_learning.curriculum").joinpath("catalogs/bnu_math_g5_upper_2024.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_catalog(payload)


def load_catalogs(directory: Path | None = None) -> list[dict[str, Any]]:
    """Discover every packaged course without coupling the UI to one grade."""
    root = Path(directory) if directory is not None else files("qingzi_learning.curriculum").joinpath("catalogs")
    catalogs = [validate_catalog(json.loads(item.read_text(encoding="utf-8")))
                for item in sorted(root.iterdir(), key=lambda item: item.name)
                if item.name.endswith(".json")]
    if not catalogs:
        raise ValueError("课程目录为空")
    ids = [item["catalog_id"] for item in catalogs]
    if len(ids) != len(set(ids)):
        raise ValueError("课程编号重复")
    return catalogs


def validate_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("课程目录格式无效")
    if not isinstance(payload.get("catalog_id"), str) or not _ID.fullmatch(payload["catalog_id"]):
        raise ValueError("课程编号无效")
    for field in ("title", "subject", "stage", "grade", "term", "publisher", "edition"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"课程字段无效：{field}")
    if payload.get("track") not in {"school", "enrichment"}:
        raise ValueError("课程来源无效")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("课程证据缺失")
    for source in sources:
        if (not isinstance(source, dict) or source.get("kind") != "photo"
                or not isinstance(source.get("filename"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.jpg", source["filename"])
                or not isinstance(source.get("sha256"), str)
                or not _HASH.fullmatch(source["sha256"])):
            raise ValueError("课程证据文件不安全")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("课程节点缺失")
    ids: set[str] = set()
    last_page = 0
    for order, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            raise ValueError("课程节点无效")
        node_id = node.get("node_id")
        page = node.get("page")
        if (not isinstance(node_id, str) or not _ID.fullmatch(node_id) or node_id in ids
                or not isinstance(node.get("title"), str) or not node["title"].strip()
                or node.get("type") not in _NODE_TYPES
                or type(node.get("order")) is not int or node["order"] != order
                or type(page) is not int or page <= last_page or page > 9999):
            raise ValueError("课程节点重复、顺序或页码无效")
        ids.add(node_id)
        last_page = page
    edges = payload.get("edges")
    if not isinstance(edges, list):
        raise ValueError("课程关系无效")
    pairs: set[tuple[str, str, str]] = set()
    for edge in edges:
        if (not isinstance(edge, dict) or edge.get("from") not in ids
                or edge.get("to") not in ids or edge["from"] == edge["to"]
                or edge.get("type") not in _EDGE_TYPES
                or edge.get("basis") != "suggestion"):
            raise ValueError("课程关系端点或依据无效")
        key = (edge["from"], edge["to"], edge["type"])
        if key in pairs:
            raise ValueError("课程关系重复")
        pairs.add(key)
    return payload
