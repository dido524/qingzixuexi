# 北师大版五年级上册数学课程知识图实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从家长实拍目录建立版本化课程图，同时导出离线可视化和 Obsidian 兼容笔记，并在知识库首页提供入口。

**Architecture:** 校验器读取随包分发的版本化 JSON，只把实拍目录作为课程结构事实。课程导出器使用现有安全路径与原子发布设施生成独立 HTML/Markdown，首页链接它们；原有判卷和掌握度数据库不变。

**Tech Stack:** Python 3.11+、标准库 JSON/HTML、现有 SQLite repository 和 publication、pytest、PyInstaller。

**Spec:** `docs/superpowers/specs/2026-09-19-bnu-math-curriculum-graph-design.md`

## Global Constraints

- 用户照片是这本实体书目录的权威证据；不能导入搜索结果中的旧版单元顺序。
- 不修改判卷、家长确认、题目统计或模型调用。
- 代码仓库不收录三张教材照片；备份原图只在用户知识库中。
- 新目录必须支持明确的教材版本、学段年级、学期、学习来源，不能硬编码五年级。
- 所有生成文件必须在 `knowledge_root` 内，内部链接必须存在。

## Review Focus

1. 同名知识点出现在学校与兴趣班时，不得自动合并；测试两个不同 `catalog_id` 可独立导出。
2. 目录有悬空边或重复节点时应拒绝，不能发布半张图；测试校验器异常。
3. 输入标题含 HTML 特殊字符时只能显示文本，不能执行；测试导出 HTML 转义。
4. 重复导出不应改写数据库或生成不同内容；测试字节级幂等和事实计数不变。
5. 页面显示“先修”等关系时必须标为整理建议，不得伪称目录原文；测试标识文案。

---

### Task 1: 版本化课程目录与校验

**Files:**
- Create: `src/qingzi_learning/curriculum/__init__.py`
- Create: `src/qingzi_learning/curriculum/catalog.py`
- Create: `src/qingzi_learning/curriculum/catalogs/bnu_math_g5_upper_2024.json`
- Modify: `pyproject.toml`
- Test: `tests/curriculum/test_catalog.py`

**Interfaces:**
- Produces: `load_catalog(path: Path | None = None) -> dict[str, Any]`, `load_catalogs(directory: Path | None = None) -> list[dict[str, Any]]`, and `validate_catalog(payload: dict[str, Any]) -> dict[str, Any]`.
- Consumers: Task 2 calls `load_catalog()` and can supply a synthetic validated catalog to export another grade/track.

- [ ] **Step 1: Write failing tests** for exact photographed sequence/pages, distinct catalog IDs, and duplicate/dangling/unsafe path rejection.
- [ ] **Step 2: Run** `python -m pytest tests/curriculum/test_catalog.py -q`; expect import or assertion failures.
- [ ] **Step 3: Implement** a small standard-library validator with stable IDs, allowed node/link types and page/order checks; add the 2024 BNU catalog with exact eight units, two practices, one math-play entry, and review.
- [ ] **Step 4: Run** the focused suite and correct only observed failures.
- [ ] **Step 5: Commit** the catalog, validator and tests.

### Task 2: 离线课程图与 Obsidian 导出

**Files:**
- Create: `src/qingzi_learning/curriculum/exporter.py`
- Modify: `src/qingzi_learning/storage/paths.py`
- Test: `tests/curriculum/test_exporter.py`

**Interfaces:**
- Consumes: `load_catalog()` and an existing `KnowledgePaths` instance.
- Produces: `CurriculumExporter(paths, catalog=None).export() -> Path` (HTML entry) and `.expected_paths() -> set[Path]`; separate system-generated Markdown from write-once parent-editable notes so updates cannot overwrite family annotations.

- [ ] **Step 1: Write failing tests** for contained paths, fully resolved Markdown links, HTML escaping, suggestion labels, source metadata, alternate-track catalog and repeatable output.
- [ ] **Step 2: Run** `python -m pytest tests/curriculum/test_exporter.py -q`; expect import or behavior failures.
- [ ] **Step 3: Implement** guarded `curriculum_file` paths plus one standalone HTML graph, a course index Markdown and one note per catalog node. Use existing `write_output` for atomic staging.
- [ ] **Step 4: Run** focused tests and `git diff --check`.
- [ ] **Step 5: Commit** exporter, paths and tests.

### Task 3: 首页和发布清单联动

**Files:**
- Modify: `src/qingzi_learning/export/dashboard.py`
- Test: `tests/export/test_dashboard.py`
- Test: `tests/workflow/test_controller.py`

**Interfaces:**
- Consumes: `CurriculumExporter.export()` / `.expected_paths()`.
- Produces: always-existing HTML link from the dashboard's mathematics knowledge-map section for each discovered curriculum.

- [ ] **Step 1: Write failing tests** that the dashboard links the generated course map and publication `current()` accepts its files while preserving old stats.
- [ ] **Step 2: Run** focused tests; expect link/output-set failure.
- [ ] **Step 3: Call** the curriculum exporter before rendering dashboard HTML; include all curriculum files in `expected_paths`; show a provenance note beside the link.
- [ ] **Step 4: Run** export/workflow focused tests and correct only observed failures.
- [ ] **Step 5: Commit** integration and tests.

### Task 4: Source evidence, package, and final validation

**Files:**
- Modify: `scripts/build.ps1`
- Modify: `docs/verification/v1-acceptance.md`
- Test: `tests/curriculum/test_catalog.py`, `tests/curriculum/test_exporter.py`, full `tests/`

**Interfaces:**
- Consumes: exact three user photos and SHA-256 listed in `findings.md`.
- Produces: packaged catalog, validated knowledge-base HTML/Markdown, private photo archive in user's knowledge root.

- [ ] **Step 1: Add a package test** that the catalog resource is discoverable from both source and PyInstaller's `_internal` tree.
- [ ] **Step 2: Update** build data inclusion, copy the three source images only to the user's private knowledge-base archive without overwrite, and verify hashes.
- [ ] **Step 3: Run** focused tests, full tests, build, `--smoke-check`, and inspect generated HTML/Markdown links against actual files.
- [ ] **Step 4: If installing**, first check the program is closed and make a SQLite online backup; then install and verify desktop shortcut/icon and existing 11/112 data.
- [ ] **Step 5: Commit and push** the exact verified source tree; report any live UI checks not run rather than implying they passed.
