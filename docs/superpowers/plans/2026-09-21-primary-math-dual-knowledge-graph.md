# Primary Math Dual-View Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current curriculum card page with one canonical primary-math graph rendered as both a child-facing panoramic association mind map and a parent-facing interactive mastery graph.

**Architecture:** A bundled, versioned JSON graph owns canonical concepts, explicit relations, aliases, source mappings, and curriculum mappings. A pure Python projection layer joins only confirmed exact mappings to existing repository facts and produces one escaped JSON view model; two self-contained offline HTML renderers consume that same view model so names, relations, evidence, and mastery states cannot diverge.

**Tech Stack:** Python 3.11 dataclasses and JSON, existing `KnowledgeRepository`/publication pipeline, self-contained HTML/CSS/vanilla JavaScript/SVG, pytest, PyInstaller packaging.

**Spec:** `docs/superpowers/specs/2026-09-21-primary-math-dual-knowledge-graph-design.md`

## Global Constraints

- Keep the national-standard four-domain spine separate from textbook, calculation-workbook, tutoring, and scanned-evidence sources.
- Treat stage, grade, term, publisher, and core competencies as metadata and filters rather than root branches.
- Only mappings whose status is `confirmed` may change a concept's displayed mastery; `pending`, `unmapped`, and `cross_subject` stay visible only in the audit panel.
- Preserve every existing fact row, parent-owned Markdown note, legacy curriculum page, and existing publication ownership rule.
- Generate both graph pages without a CDN or network access; all HTML, CSS, JavaScript, SVG, and data must be embedded locally.
- Use the approved Qingzi soft blue-purple visual language; domain fill and mastery border must remain separate encodings, and status must also have text/icon cues.
- No graph node may be called mastered from one or two answers; evidence count and recency remain visible separately from weighted correctness.
- New grades, books, calculation series, and tutoring courses must be addable through validated data files without renderer changes.

## Review Focus

- A knowledge label that looks mathematical but has no exact confirmed alias must stay unaggregated and appear under `unmapped`, never silently attach by fuzzy matching; Task 2 pins this.
- English grammar labels currently stored under math must be classified `cross_subject` and contribute zero evidence to math nodes; Task 2 pins this.
- A node with one correct answer must show `evidence_insufficient`, not `stable`; Task 2 pins this.
- Special characters from imported course titles, knowledge labels, or notes must not break HTML or execute script; Task 3 pins both HTML escaping and safe JSON embedding.
- Small/restored windows and print layout must keep controls and legends usable while both pages remain fully offline; Tasks 4, 5, and 7 pin responsive and print markers plus browser smoke tests.

---

### Task 1: Canonical Graph Model and Versioned Primary-Math Data

**Files:**
- Create: `src/qingzi_learning/curriculum/graph.py`
- Create: `src/qingzi_learning/curriculum/graphs/primary_math_v1.json`
- Modify: `pyproject.toml`
- Test: `tests/curriculum/test_graph.py`

**Interfaces:**
- Consumes: no new project interfaces.
- Produces: `GraphNode`, `GraphEdge`, `SourceMapping`, `MathGraph`, `load_math_graph(path: Path | None = None) -> MathGraph`, and `validate_math_graph(payload: dict[str, Any]) -> MathGraph`.

- [ ] **Step 1: Write failing validation and topology tests**

```python
def test_primary_math_graph_has_official_spine_and_cross_links():
    graph = load_math_graph()
    assert [graph.nodes[node_id].label for node_id in graph.root_ids] == [
        "数与代数", "图形与几何", "统计与概率", "综合与实践",
    ]
    assert {"prerequisite", "deepens_to", "applies_to", "confusable"} <= {
        edge.kind for edge in graph.edges
    }
    assert any(edge.source == "number-fractions" and edge.target == "relation-ratio"
               for edge in graph.edges)


@pytest.mark.parametrize("mutation", [
    lambda data: data["nodes"].append(data["nodes"][0]),
    lambda data: data["edges"].append({"source": "missing", "target": "number-fractions",
                                        "kind": "prerequisite", "basis": "official"}),
    lambda data: data["nodes"][0].update(parent_id="missing"),
    lambda data: data["nodes"][0].update(aliases=["重复别名"]),
])
def test_graph_rejects_duplicate_or_dangling_content(mutation):
    payload = json.loads(GRAPH_PATH.read_text("utf-8"))
    mutation(payload)
    with pytest.raises(ValueError):
        validate_math_graph(payload)
```

- [ ] **Step 2: Run tests and verify the module/data are absent**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_graph.py -v`

Expected: collection fails because `qingzi_learning.curriculum.graph` does not exist.

- [ ] **Step 3: Implement immutable graph dataclasses and strict validation**

```python
@dataclass(frozen=True)
class GraphNode:
    node_id: str
    label: str
    kind: str
    domain: str
    parent_id: str | None
    stages: tuple[str, ...]
    grades: tuple[str, ...]
    competencies: tuple[str, ...]
    aliases: tuple[str, ...]
    official: bool


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str
    basis: str
    importance: int


@dataclass(frozen=True)
class SourceMapping:
    source_id: str
    source_label: str
    source_type: str
    target_ids: tuple[str, ...]
    relation: str
    status: str
    basis: str


@dataclass(frozen=True)
class MathGraph:
    graph_id: str
    version: int
    root_ids: tuple[str, ...]
    nodes: Mapping[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    source_mappings: tuple[SourceMapping, ...]
```

Validation must reject duplicate IDs/aliases, dangling parents/edges/mappings, cycles in `parent_id`, unknown relation/status/source types, unsafe IDs, empty required text, invalid grade/stage values, and an official node parented below a non-official extension node.

- [ ] **Step 4: Add the canonical data and package rule**

Seed all four official domains and their official themes, plus primary-school concept nodes for integer/decimal/fraction operations, factors and multiples, quantity relations, equations, ratio and proportion, plane/solid geometry, measurement, position and movement, data displays, averages/percent statistics, possibility, theme activities, and project learning. Add extension groups for number theory, counting/combinatorics, spatial reasoning, and logic/strategy. Include important cross-links such as fractions→ratio, decimals→area calculation, percent→statistical charts, factors/multiples→fraction simplification, equations→application modelling, and rotation/symmetry→design practice.

Add `graphs/*.json` to `qingzi_learning.curriculum` package data in `pyproject.toml`.

- [ ] **Step 5: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_graph.py -v`

Expected: all graph validation and topology tests pass.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src/qingzi_learning/curriculum/graph.py src/qingzi_learning/curriculum/graphs/primary_math_v1.json tests/curriculum/test_graph.py
git commit -m "feat: add canonical primary math knowledge graph"
```

### Task 2: Confirmed Mapping Audit and Conservative Mastery Projection

**Files:**
- Create: `src/qingzi_learning/curriculum/mastery.py`
- Create: `src/qingzi_learning/curriculum/mappings/bnu_math_g5_upper_2024.json`
- Modify: `pyproject.toml`
- Test: `tests/curriculum/test_mastery.py`

**Interfaces:**
- Consumes: `MathGraph`; `KnowledgeRepository.export_subject_snapshot("数学") -> dict[str, Any]`.
- Produces: `MasteryProjection`, `ConceptMastery`, `MappingAuditItem`, `build_mastery_projection(graph: MathGraph, snapshot: dict[str, Any], now: datetime | None = None) -> MasteryProjection`, and `load_course_mappings(path: Path | None = None) -> tuple[SourceMapping, ...]`.

- [ ] **Step 1: Write failing mapping and evidence tests**

```python
def test_only_exact_confirmed_aliases_contribute():
    snapshot = math_snapshot([
        point("小数乘法", exposure_count=6, correct_count=4, partial_count=1, incorrect_count=1),
        point("小数乘法易错", exposure_count=9, correct_count=0, partial_count=0, incorrect_count=9),
    ])
    projection = build_mastery_projection(load_math_graph(), snapshot, now=NOW)
    assert projection.by_concept["number-decimal-multiply"].evidence_count == 6
    assert projection.audit_by_label["小数乘法易错"].status == "unmapped"


@pytest.mark.parametrize("label", ["情态动词can", "like doing", "would like to do", "一般将来时"])
def test_english_grammar_under_math_is_quarantined(label):
    projection = build_mastery_projection(load_math_graph(), math_snapshot([point(label)]), now=NOW)
    assert projection.audit_by_label[label].status == "cross_subject"
    assert sum(item.evidence_count for item in projection.by_concept.values()) == 0


def test_two_correct_answers_are_not_called_mastered():
    projection = build_mastery_projection(
        load_math_graph(), math_snapshot([point("小数乘法", exposure_count=2, correct_count=2)]), now=NOW
    )
    assert projection.by_concept["number-decimal-multiply"].state == "evidence_insufficient"
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_mastery.py -v`

Expected: failure because the projection module is absent.

- [ ] **Step 3: Implement exact-alias audit and mastery rules**

```python
@dataclass(frozen=True)
class ConceptMastery:
    concept_id: str
    state: str
    weighted_rate: float | None
    evidence_count: int
    recent_count: int
    last_seen_at: str | None
    trend: str


@dataclass(frozen=True)
class MappingAuditItem:
    label: str
    status: str
    concept_ids: tuple[str, ...]
    evidence_count: int


@dataclass(frozen=True)
class MasteryProjection:
    by_concept: Mapping[str, ConceptMastery]
    audit: tuple[MappingAuditItem, ...]
```

Use normalized exact matching only (Unicode trim and internal-space collapse, no substring/fuzzy matching). Classify explicit English grammar patterns as `cross_subject`; all other unmatched labels are `unmapped`. Compute weighted rate from confirmed repository aggregates. Use `evidence_insufficient` below 5 exposures; for 5+ use `stable >= .85`, `basic >= .70`, `unstable >= .50`, otherwise `weak`. Keep `not_learned` for graph nodes with no evidence and outside the selected grade scope. Trend is `unknown` unless the repository supplies enough recent evidence.

- [ ] **Step 4: Add explicit current-book source mappings**

Create validated mappings from each photographed BNU unit to one or more canonical concept nodes. Mark directory-grounded broad theme mappings `confirmed`; keep any inferred under-unit detail `pending`. Include `source_type: textbook`, catalog ID, unit ID, relation `curriculum_covers`, and basis text. Add `mappings/*.json` to package data.

- [ ] **Step 5: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_mastery.py tests/curriculum/test_graph.py -v`

Expected: all tests pass and no unmatched label affects any canonical concept.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src/qingzi_learning/curriculum/mastery.py src/qingzi_learning/curriculum/mappings/bnu_math_g5_upper_2024.json tests/curriculum/test_mastery.py
git commit -m "feat: project confirmed math evidence onto graph"
```

### Task 3: Shared Offline View Model and Safe HTML Shell

**Files:**
- Create: `src/qingzi_learning/curriculum/view_model.py`
- Create: `src/qingzi_learning/curriculum/html_shell.py`
- Test: `tests/curriculum/test_graph_html.py`

**Interfaces:**
- Consumes: `MathGraph`, `MasteryProjection`, course mappings.
- Produces: `build_graph_view_model(graph, projection, mappings) -> dict[str, Any]`, `safe_embedded_json(payload: dict[str, Any]) -> str`, and `render_graph_shell(*, title: str, mode: str, payload: dict[str, Any], body: str, styles: str, script: str) -> str`.

- [ ] **Step 1: Write failing shared-model and escaping tests**

```python
def test_shared_view_model_carries_nodes_edges_sources_and_audit():
    model = build_graph_view_model(graph, projection, mappings)
    assert {"nodes", "edges", "sources", "audit", "legend", "filters"} <= model.keys()
    assert model["nodes"]["number-decimal-multiply"]["mastery"]["evidence_count"] == 6


def test_shell_escapes_markup_and_script_terminators():
    page = render_graph_shell(
        title='<img src=x onerror="alert(1)">', mode="panorama",
        payload={"label": "</script><script>alert(1)</script>"}, body="", styles="", script="",
    )
    assert '<img src=x onerror="alert(1)">' not in page
    assert "</script><script>alert(1)</script>" not in page
    assert "&lt;img" in page
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_graph_html.py -v`

Expected: failure because the shared view modules are absent.

- [ ] **Step 3: Implement one canonical serialization boundary**

The node payload must include `id`, `label`, `kind`, `domain`, `parentId`, `stages`, `grades`, `competencies`, `official`, `mastery`, and matching `sources`. Edges include `source`, `target`, `kind`, `basis`, and `importance`. Audit entries never appear as graph nodes. Serialize with `ensure_ascii=False`, replace `<`, `>`, `&`, U+2028, and U+2029 with escaped Unicode sequences, and HTML-escape title/visible shell text.

- [ ] **Step 4: Add the shared responsive/accessible shell**

Provide skip link, toolbar landmark, persistent legend, graph viewport, details drawer, empty-state region, `aria-live` status, keyboard-visible focus, `prefers-reduced-motion`, and `@media print` hooks. Do not include external `<script src>`, `<link href>`, fonts, or images.

- [ ] **Step 5: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_graph_html.py -v`

Expected: escaping, offline dependency, and common view-model tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingzi_learning/curriculum/view_model.py src/qingzi_learning/curriculum/html_shell.py tests/curriculum/test_graph_html.py
git commit -m "feat: add shared offline math graph view model"
```

### Task 4: Child-Facing Panoramic Association Mind Map

**Files:**
- Create: `src/qingzi_learning/curriculum/panorama.py`
- Test: `tests/curriculum/test_panorama.py`

**Interfaces:**
- Consumes: shared graph view model.
- Produces: `render_panorama_page(model: dict[str, Any]) -> str`.

- [ ] **Step 1: Write failing structure and interaction-contract tests**

```python
def test_panorama_is_not_only_a_tree():
    page = render_panorama_page(model)
    assert 'data-edge-kind="contains"' in page
    assert 'data-edge-kind="applies_to"' in page
    assert 'data-edge-kind="prerequisite"' in page
    assert "显示重要联系" in page and "显示全部联系" in page


def test_panorama_has_child_view_controls_and_print_contract():
    page = render_panorama_page(model)
    for marker in ("全部小学", "当前年级", "本学期", "恢复全貌", "全屏", "打印"):
        assert marker in page
    assert "@media print" in page and "prefers-reduced-motion" in page
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_panorama.py -v`

Expected: failure because the panorama renderer is absent.

- [ ] **Step 3: Implement deterministic four-zone SVG layout**

Place the four domains in fixed quadrants around the center, themes on the first ring, concepts on subsequent rings, and extension nodes in a visually separated outer band. Render containment edges first and important cross-links above them. Use domain fill colors `#E8F1FF`, `#EEE9FF`, `#E7F7F1`, `#FFF1E8`; encode mastery with border class and a text/icon badge. Keep labels horizontal and wrap them into bounded `foreignObject`/SVG text blocks.

- [ ] **Step 4: Implement child interactions**

Add pointer/wheel pan and zoom, keyboard zoom, fit-to-screen, search, full-screen, scope toggles (`all_primary`, `current_grade`, `current_term`), important/all relation switch, node focus with connected-path highlighting, and a simple drawer headed “我在哪里学过 / 和什么有关 / 最近掌握情况”. Persist no private data in browser storage.

- [ ] **Step 5: Implement print projection**

At print time hide toolbars/drawer, fit the important-link graph into a landscape page, show a compact legend and generation date, and retain mastery text markers so grayscale printing remains understandable.

- [ ] **Step 6: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_panorama.py tests/curriculum/test_graph_html.py -v`

Expected: page contract, shared escaping, offline, and cross-link tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/qingzi_learning/curriculum/panorama.py tests/curriculum/test_panorama.py
git commit -m "feat: add panoramic math association map"
```

### Task 5: Parent-Facing Interactive Mastery Graph

**Files:**
- Create: `src/qingzi_learning/curriculum/explorer.py`
- Test: `tests/curriculum/test_explorer.py`

**Interfaces:**
- Consumes: shared graph view model.
- Produces: `render_explorer_page(model: dict[str, Any]) -> str`.

- [ ] **Step 1: Write failing filter, shortcoming, and detail tests**

```python
def test_explorer_has_required_filters_and_shortcoming_mode():
    page = render_explorer_page(model)
    for marker in ("学段", "年级", "学期", "教材与课程", "掌握状态", "核心素养", "短板模式"):
        assert marker in page
    assert "前置知识路径" in page and "确认题目" in page


def test_audit_items_stay_out_of_graph_nodes():
    page = render_explorer_page(model_with_cross_subject_label)
    payload = extract_payload(page)
    assert "情态动词can" not in {node["label"] for node in payload["nodes"].values()}
    assert any(item["label"] == "情态动词can" and item["status"] == "cross_subject"
               for item in payload["audit"])
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_explorer.py -v`

Expected: failure because the explorer renderer is absent.

- [ ] **Step 3: Implement layered graph layout and filters**

Render official domains/themes as the stable backbone, expand/collapse descendants, and draw non-hierarchical relations as typed curves. Implement filters for stage, grade, term, source type/source label, mastery state, and competency. Add source toggles for school textbook, calculation training, tutoring, and all sources. Filtering must hide, never delete, nodes and must keep ancestors needed for context.

- [ ] **Step 4: Implement shortcoming mode and evidence drawer**

Shortcoming mode shows `weak`/`unstable` nodes plus their ancestor and prerequisite paths, while dimming stable nodes. The drawer shows definition/position, prerequisite and downstream relations, textbook/training sources, weighted rate, evidence count, last-seen time, trend label, and confirmed question summary links. Add a separate “待整理映射” panel for `pending`, `unmapped`, and `cross_subject` labels.

- [ ] **Step 5: Add responsive and print behavior**

Use a collapsible filter rail below 960 px, keep toolbar actions in a wrapping sticky row, and ensure the graph has a minimum usable viewport height after maximize/restore. Print the currently filtered graph and a tabular weak-point appendix.

- [ ] **Step 6: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_explorer.py tests/curriculum/test_graph_html.py -v`

Expected: filter, shortcoming, audit separation, escaping, and offline tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/qingzi_learning/curriculum/explorer.py tests/curriculum/test_explorer.py
git commit -m "feat: add interactive math mastery graph"
```

### Task 6: Dual-View Export, Obsidian Notes, Dashboard, and Publication Integration

**Files:**
- Create: `src/qingzi_learning/curriculum/graph_exporter.py`
- Modify: `src/qingzi_learning/storage/paths.py`
- Modify: `src/qingzi_learning/export/dashboard.py`
- Modify: `src/qingzi_learning/curriculum/exporter.py`
- Test: `tests/curriculum/test_graph_exporter.py`
- Modify: `tests/export/test_dashboard.py`
- Modify: `tests/export/test_publication_lock.py`

**Interfaces:**
- Consumes: repository, graph loader, course mappings, mastery projection, both renderers.
- Produces: `MathKnowledgeGraphExporter(repo: KnowledgeRepository, paths: KnowledgePaths | None = None)`, `export() -> tuple[Path, Path]`, `expected_paths() -> set[Path]`, and `KnowledgePaths.knowledge_graph_file(subject: str, graph_id: str, filename: str) -> Path`.

- [ ] **Step 1: Write failing integration tests**

```python
def test_exporter_publishes_two_pages_from_one_payload(repo):
    exporter = MathKnowledgeGraphExporter(repo)
    panorama, explorer = exporter.export()
    assert panorama.name == "数学知识全景脑图.html"
    assert explorer.name == "数学掌握知识图谱.html"
    assert extract_payload(panorama.read_text("utf-8")) == extract_payload(explorer.read_text("utf-8"))
    assert exporter.expected_paths() <= set(panorama.parent.iterdir())


def test_dashboard_links_both_views_without_removing_legacy_course_page(repo):
    exporter = DashboardExporter(repo)
    page = exporter.export().read_text("utf-8")
    assert "数学知识全景脑图" in page and "数学掌握知识图谱" in page
    assert "北师大版数学五年级上册课程结构" in page
    assert all(path.is_file() for path in exporter.expected_paths())
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum/test_graph_exporter.py tests/export/test_dashboard.py -v`

Expected: failure because the new exporter and paths do not exist.

- [ ] **Step 3: Implement guarded output paths and dual-view exporter**

Publish under `数学/课程体系/primary-math-v1/` with filenames `数学知识全景脑图.html`, `数学掌握知识图谱.html`, `系统知识图谱总览.md`, and one `系统-<concept-id>.md` per canonical concept. Keep `我的知识图谱笔记.md` and `我的-<concept-id>.md` parent-owned and create-once. System notes use ordinary relative Markdown links for Obsidian and include relation/source/mastery summaries without plugin syntax.

- [ ] **Step 4: Integrate with dashboard and manifest**

Construct one `MathKnowledgeGraphExporter` in `DashboardExporter` when math is configured; export its files before publishing dashboard links; include system outputs in `expected_paths()` and parent notes in `parent_note_paths()`. Replace the old single math course link copy with two prominent view cards plus a secondary “教材目录证据页” link. Leave other subjects and legacy curriculum exports unchanged.

- [ ] **Step 5: Preserve legacy curriculum entry behavior**

Keep `CurriculumExporter.entry_path()` and all prior expected files intact so old bookmarks and publication manifests can be regenerated. Add only a small backlink from the legacy course page to the new dual-view index when it exists; never overwrite parent notes.

- [ ] **Step 6: Run integration tests**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum tests/export/test_dashboard.py tests/export/test_publication_lock.py -v`

Expected: dual-view integration passes, old curriculum tests still pass, and the atomic publication output set exactly matches all generated files.

- [ ] **Step 7: Commit**

```powershell
git add src/qingzi_learning/curriculum/graph_exporter.py src/qingzi_learning/storage/paths.py src/qingzi_learning/export/dashboard.py src/qingzi_learning/curriculum/exporter.py tests/curriculum/test_graph_exporter.py tests/export/test_dashboard.py tests/export/test_publication_lock.py
git commit -m "feat: publish dual-view math knowledge graph"
```

### Task 7: Browser, Real-Data, Packaging, and Installation Verification

**Files:**
- Create: `scripts/verify_math_knowledge_graph.py`
- Create: `docs/verification/math-dual-knowledge-graph-acceptance.md`
- Modify: `tests/test_packaging_assets.py`

**Interfaces:**
- Consumes: installed/built package, a source SQLite database opened read-only or copied to a temporary verification root, generated HTML files.
- Produces: command-line verification report with counts for canonical nodes, cross-links, confirmed/unmapped/cross-subject labels, evidence-bearing nodes, broken links, and offline assets.

- [ ] **Step 1: Write failing packaging and verification-contract tests**

```python
def test_package_contains_graph_and_mapping_data():
    assert packaged("qingzi_learning/curriculum/graphs/primary_math_v1.json")
    assert packaged("qingzi_learning/curriculum/mappings/bnu_math_g5_upper_2024.json")


def test_verifier_rejects_external_assets_or_broken_links(tmp_path):
    result = verify_graph_pages(tmp_path, pages_with_external_script_and_missing_note())
    assert not result.ok
    assert {"external_asset", "broken_link"} <= set(result.error_codes)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_packaging_assets.py -v`

Expected: new graph/mapping package assertions fail until build configuration and verifier are complete.

- [ ] **Step 3: Implement deterministic verifier**

The verifier must parse both embedded payloads and assert equality, validate every local link under the configured knowledge root, reject HTTP(S) assets, count relation types, ensure at least one important non-hierarchical cross-link, report quarantined labels without exposing question text, and assert that no `cross_subject`/`unmapped` evidence entered node totals.

- [ ] **Step 4: Run the complete automated suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass with no skipped graph tests.

- [ ] **Step 5: Verify against a safe copy of the real knowledge database**

Copy `C:\Users\Home\AppData\Local\QingziLearningAssistant\knowledge.sqlite3` and its required knowledge-root files to a timestamped temporary directory, run the exporter and verifier there, and record only aggregate counts in the acceptance document. Confirm that the known English grammar labels are quarantined and that source database size/hash are unchanged.

- [ ] **Step 6: Perform browser visual acceptance**

Open both temporary pages in the local browser. At desktop width and a reduced/restored window, verify toolbar wrapping, persistent legend, readable labels, pan/zoom, search, scope filters, shortcoming mode, details drawer, full-screen, and print preview. Capture screenshots for the acceptance record; no remote request may appear in the browser network log.

- [ ] **Step 7: Build and inspect the packaged application**

Run: `.venv\Scripts\python.exe -m PyInstaller --noconfirm 晴子学习助手.spec`

Then run the packaged asset test and a clean temporary-root export from `dist`. Expected: both graph and mapping JSON resources load from the packaged executable and both HTML pages pass the verifier.

- [ ] **Step 8: Install only after closing the running app and making a recoverable backup**

Resolve the exact installed application directory, back up the executable and the SQLite database to timestamped sibling files/directories, replace only the application build, launch it, regenerate the dashboard, and open both graph views. Do not delete the backup after verification.

- [ ] **Step 9: Record acceptance evidence and commit**

```powershell
git add scripts/verify_math_knowledge_graph.py docs/verification/math-dual-knowledge-graph-acceptance.md tests/test_packaging_assets.py
git commit -m "test: verify dual-view math graph release"
```

### Task 8: Final Regression and Release Commit

**Files:**
- Modify: `docs/verification/math-dual-knowledge-graph-acceptance.md`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: verified release evidence and a clean, reviewable branch.

- [ ] **Step 1: Run focused graph regression**

Run: `.venv\Scripts\python.exe -m pytest tests/curriculum tests/export/test_dashboard.py tests/export/test_publication_lock.py tests/test_packaging_assets.py -q`

Expected: all focused tests pass.

- [ ] **Step 2: Run full regression**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: complete suite passes.

- [ ] **Step 3: Check repository integrity**

Run: `git diff --check` and `git status --short --branch`.

Expected: no whitespace errors and only the intended verification-document update remains.

- [ ] **Step 4: Add final observed versions, commands, counts, screenshots, backup paths, and installation result to the acceptance document**

Record concrete command output and observed artifacts; do not write “pass” without evidence.

- [ ] **Step 5: Commit final acceptance evidence**

```powershell
git add docs/verification/math-dual-knowledge-graph-acceptance.md
git commit -m "docs: record dual-view math graph acceptance"
```

- [ ] **Step 6: Inspect the full branch diff before reporting completion**

Run: `git log --oneline --decorate -12` and `git diff 829833d..HEAD --stat`.

Expected: the design, plan, implementation, tests, and verification commits are present; unrelated user files are absent.
