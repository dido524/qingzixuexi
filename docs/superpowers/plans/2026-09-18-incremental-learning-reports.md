# Incremental Learning Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, incremental child and parent learning reports with a dedicated desktop center and safe A4 printing.

**Architecture:** SQLite stores immutable report runs and their complete evidence snapshots. A deterministic profile builder computes facts and deltas; a schema-constrained narrative provider may improve the wording, while a local template is always available. Focused HTML renderers publish child and parent artifacts atomically, and the Tk UI controls generation, history, opening, and print views through the existing single worker thread.

**Tech Stack:** Python 3.13, SQLite, Tkinter, JSON Schema, Codex CLI, self-contained HTML/CSS, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-17-learning-reports-and-targeted-exams-design.md`

## Global Constraints

- The knowledge root remains exactly `C:\晴子知识库\5th grade`.
- Subjects remain exactly `语文`, `数学`, and `英语`.
- Facts, deltas, evidence levels, sample counts, and priorities are computed locally; the model cannot change them.
- A report run is immutable after completion; generation also refreshes `学习报告\最新学情报告.html`.
- Only confirmed questions or non-review questions with confidence at least `0.80` count as effective evidence.
- Child copy must avoid rankings, shame labels, exaggerated praise, and unsupported conclusions.
- Report generation must fall back to local copy if Codex is unavailable or its response is invalid.
- All output paths use the existing guarded root and atomic-write mechanisms.
- Printing must show the Windows/browser print dialog and must never silently print.
- No child image, full knowledge-base dump, API key, or unrelated personal information is sent to the model.

---

### Task 1: Persist immutable report runs

**Files:**
- Modify: `src/qingzi_learning/storage/schema.sql`
- Modify: `src/qingzi_learning/storage/repository.py`
- Test: `tests/storage/test_report_repository.py`

**Interfaces:**
- Produces: `ReportRun` dataclass.
- Produces: `KnowledgeRepository.create_report_run(report_id: str, previous_report_id: str | None, evidence_cutoff_at: str, snapshot: dict) -> ReportRun`.
- Produces: `KnowledgeRepository.complete_report_run(report_id: str, narrative: dict, output_files: dict[str, str]) -> ReportRun`.
- Produces: `KnowledgeRepository.fail_report_run(report_id: str, error_code: str) -> ReportRun`.
- Produces: `KnowledgeRepository.get_report_run(report_id: str) -> ReportRun | None`, `latest_completed_report() -> ReportRun | None`, and `list_report_runs() -> tuple[ReportRun, ...]`.

- [ ] **Step 1: Write repository tests before adding the table**

```python
def test_report_run_lifecycle_is_immutable_after_completion(repo):
    created = repo.create_report_run("report-1", None, "2026-09-18T00:00:00Z", {"subjects": {}})
    assert created.status == "generating"
    completed = repo.complete_report_run("report-1", {"summary": "ok"}, {"child": "学习报告/a.html"})
    assert completed.status == "completed"
    with pytest.raises(ValueError, match="已完成"):
        repo.complete_report_run("report-1", {}, {})

def test_latest_completed_report_ignores_failed_and_generating_runs(repo):
    repo.create_report_run("old", None, "2026-09-17T00:00:00Z", {"version": 1})
    repo.complete_report_run("old", {}, {"child": "old.html"})
    repo.create_report_run("failed", "old", "2026-09-18T00:00:00Z", {"version": 2})
    repo.fail_report_run("failed", "narrative_failed")
    assert repo.latest_completed_report().report_id == "old"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\storage\test_report_repository.py -q`

Expected: collection or attribute failure because report persistence does not exist.

- [ ] **Step 3: Add the table and repository API**

Add a `report_runs` table with primary key `report_id`, constrained status (`generating`, `completed`, `failed`), previous report foreign key, cutoff, snapshot JSON, narrative JSON, output-files JSON, error code, and timestamps. Serialize with the repository's `_json` path, reject duplicate IDs, update only `generating` rows, and parse JSON into a frozen `ReportRun` dataclass.

- [ ] **Step 4: Run storage tests and the existing repository suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests\storage\test_report_repository.py tests\storage\test_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the storage slice**

```powershell
git add src/qingzi_learning/storage/schema.sql src/qingzi_learning/storage/repository.py tests/storage/test_report_repository.py
git commit -m "feat: persist immutable learning report runs"
```

### Task 2: Build deterministic cumulative and incremental profiles

**Files:**
- Create: `src/qingzi_learning/reporting/__init__.py`
- Create: `src/qingzi_learning/reporting/profile.py`
- Modify: `src/qingzi_learning/storage/repository.py`
- Test: `tests/reporting/test_profile.py`

**Interfaces:**
- Consumes: existing effective-question and knowledge-stat facts.
- Produces: `KnowledgeRepository.report_evidence_rows(cutoff_at: str) -> dict[str, object]` with effective questions, pending count, documents, study dates, and knowledge stats.
- Produces: `evidence_level(question_count: int, study_day_count: int) -> str` returning `no_data`, `initial`, `forming`, or `stable`.
- Produces: `LearningProfileBuilder(repo).build(previous_snapshot: dict | None, *, cutoff_at: datetime) -> dict`.

- [ ] **Step 1: Write evidence-level and delta tests**

```python
@pytest.mark.parametrize(("questions", "days", "expected"), [
    (0, 0, "no_data"), (9, 3, "initial"), (10, 1, "initial"),
    (10, 2, "forming"), (29, 8, "forming"), (30, 2, "forming"), (30, 3, "stable"),
])
def test_evidence_level_is_conservative(questions, days, expected):
    assert evidence_level(questions, days) == expected

def test_profile_compares_current_facts_with_previous_snapshot(repo, seeded_learning_history):
    first = LearningProfileBuilder(repo).build(None, cutoff_at=UTC_1)
    seeded_learning_history.add_confirmed_retest(status="correct")
    second = LearningProfileBuilder(repo).build(first, cutoff_at=UTC_2)
    point = second["subjects"]["数学"]["knowledge_points"]["分数应用"]
    assert point["delta"]["correct"] == 1
    assert point["change"] == "improved"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_profile.py -q`

Expected: import failure for `qingzi_learning.reporting.profile`.

- [ ] **Step 3: Implement one bounded read query and pure profile transforms**

Query through `effective_questions`, join documents and knowledge-point links, and exclude `needs_review` plus model rows below `0.80`. Store ISO UTC cutoff, total and per-subject facts, ordered knowledge points, error-category counts, distinct study dates, and delta fields. Sort by `review_priority DESC`, then knowledge-point name so snapshots are deterministic.

- [ ] **Step 4: Verify profile tests and existing knowledge calculations**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_profile.py tests\knowledge\test_updater.py tests\export\test_dashboard.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the profile slice**

```powershell
git add src/qingzi_learning/reporting src/qingzi_learning/storage/repository.py tests/reporting/test_profile.py
git commit -m "feat: compute incremental learning profiles"
```

### Task 3: Add constrained narrative generation with local fallback

**Files:**
- Create: `src/qingzi_learning/schema/report-narrative.schema.json`
- Create: `src/qingzi_learning/reporting/narrative.py`
- Modify: `pyproject.toml`
- Modify: `scripts/build.ps1`
- Test: `tests/reporting/test_narrative.py`
- Modify: `tests/test_packaging_assets.py`

**Interfaces:**
- Produces: `NarrativeProvider` protocol with `generate(profile: dict) -> dict`.
- Produces: `LocalNarrativeProvider.generate(profile: dict) -> dict`.
- Produces: `CodexNarrativeProvider(runner=None).generate(profile: dict) -> dict`.
- Produces: `NarrativeService(primary: NarrativeProvider, fallback: NarrativeProvider).generate(profile: dict) -> dict`, adding `source` as `codex` or `local_template`.
- Produces: `validate_narrative(profile: dict, narrative: dict) -> None` that rejects unknown evidence IDs, invented numeric claims, unsafe HTML, and more than three child goals.

- [ ] **Step 1: Write contract, grounding, and fallback tests**

```python
def test_narrative_rejects_unknown_evidence_id(profile):
    bad = valid_narrative(evidence_ids=["missing-point"])
    with pytest.raises(ValueError, match="证据"):
        validate_narrative(profile, bad)

def test_local_narrative_never_uses_shame_labels(profile):
    text = json.dumps(LocalNarrativeProvider().generate(profile), ensure_ascii=False)
    assert all(word not in text for word in ("很差", "笨", "落后", "粗心大意"))

def test_codex_failure_uses_local_fallback(profile):
    service = NarrativeService(primary=FailingProvider(), fallback=LocalNarrativeProvider())
    assert service.generate(profile)["source"] == "local_template"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_narrative.py tests\test_packaging_assets.py -q`

Expected: missing module/schema assertions.

- [ ] **Step 3: Implement the schema, local copy, Codex call, and strict grounding**

Use a separate read-only Codex CLI invocation with `--output-schema`; pass only the profile's bounded evidence records. Require the model to return section text plus cited evidence IDs, never HTML or statistics. On `AnalysisError`, timeout, invalid JSON, invalid schema, unknown IDs, or unsupported numeric claims, return the deterministic local narrative.

- [ ] **Step 4: Package the new schema and run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_narrative.py tests\test_packaging_assets.py -q`

Expected: PASS and smoke-package fixtures include `report-narrative.schema.json`.

- [ ] **Step 5: Commit the narrative slice**

```powershell
git add src/qingzi_learning/schema/report-narrative.schema.json src/qingzi_learning/reporting/narrative.py pyproject.toml scripts/build.ps1 tests/reporting/test_narrative.py tests/test_packaging_assets.py
git commit -m "feat: add grounded report narratives"
```

### Task 4: Render and atomically publish child and parent reports

**Files:**
- Create: `src/qingzi_learning/reporting/render.py`
- Create: `src/qingzi_learning/reporting/service.py`
- Modify: `src/qingzi_learning/storage/paths.py`
- Modify: `src/qingzi_learning/export/dashboard.py`
- Test: `tests/reporting/test_render.py`
- Test: `tests/reporting/test_service.py`
- Modify: `tests/export/test_dashboard.py`

**Interfaces:**
- Produces: `ReportArtifacts(report_id: str, child_path: Path, parent_path: Path, latest_path: Path, manifest_path: Path)`.
- Produces: `KnowledgePaths.report_directory(year: int, month: int, report_id: str) -> Path` and `latest_report_path() -> Path`.
- Produces: `ReportRenderer.render_child(profile, narrative) -> str`, `render_parent(profile, narrative) -> str`.
- Produces: `LearningReportService.generate(*, now: datetime | None = None) -> ReportArtifacts` and `history() -> tuple[ReportRun, ...]`.

- [ ] **Step 1: Write renderer security, print, increment, and failure tests**

```python
def test_child_report_is_printable_and_contains_no_parent_detail(profile, narrative):
    text = ReportRenderer().render_child(profile, narrative)
    assert '@media print' in text and 'window.print()' in text
    assert "家长复核明细" not in text
    assert "初步观察" in text

def test_service_does_not_replace_latest_when_publication_fails(service, latest_path, monkeypatch):
    latest_path.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(service, "_publish", lambda *a: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        service.generate(now=UTC_NOW)
    assert latest_path.read_text("utf-8") == "previous"
    assert service.repo.latest_completed_report() is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_render.py tests\reporting\test_service.py -q`

Expected: missing renderer/service failures.

- [ ] **Step 3: Implement escaped self-contained HTML and rollback-safe publication**

Render separate files with mature blue/green/warm-gray styling, A4 CSS, visible sample sizes, evidence labels, and a prominent print button. Stage child, parent, manifest, and latest alias under the guarded root; replace them only after every staged digest validates. Mark the run completed only after publication succeeds, and failed otherwise. Add dashboard links only when a completed report and current file exist.

- [ ] **Step 4: Run report and export tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting tests\export -q`

Expected: PASS.

- [ ] **Step 5: Commit the export slice**

```powershell
git add src/qingzi_learning/reporting src/qingzi_learning/storage/paths.py src/qingzi_learning/export/dashboard.py tests/reporting tests/export/test_dashboard.py
git commit -m "feat: publish printable incremental reports"
```

### Task 5: Add the report tab to a dedicated learning center

**Files:**
- Create: `src/qingzi_learning/ui/learning_center.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `src/qingzi_learning/workflow/controller.py`
- Test: `tests/ui/test_learning_center.py`
- Modify: `tests/ui/test_view_model.py`

**Interfaces:**
- Produces: `WorkflowController.generate_learning_report() -> ReportArtifacts` and `report_history() -> tuple[ReportRun, ...]`.
- Extends `_Command.kind` with `list_reports` and `generate_report`.
- Extends `UiEvent` with `report_runs: tuple[ReportRun, ...]` and `report_artifacts: ReportArtifacts | None`.
- Produces: `LearningCenterDialog(parent, dialog_id, on_generate_report, on_open, on_print, on_close)`.

- [ ] **Step 1: Write worker ownership and dialog-state tests**

```python
def test_generate_report_runs_on_worker_owned_controller(worker):
    operation = worker.submit("generate_report", context="learning_center", dialog_id="center-1")
    event = next_event(worker.events, "report_generated")
    assert event.operation_id == operation
    assert event.dialog_id == "center-1"

def test_report_buttons_target_separate_files(dialog, completed_run):
    dialog.show_run(completed_run)
    assert dialog.child_print_path.name == "孩子版.html"
    assert dialog.parent_print_path.name == "家长版.html"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\ui\test_learning_center.py tests\ui\test_view_model.py -q`

Expected: missing command/event/dialog failures.

- [ ] **Step 3: Implement one scalable center and report workflow**

Add one secondary-row button named `学习与复习`; it opens a large resizable dialog rather than adding multiple footer buttons. The report tab shows evidence status, newest run, history, generation progress, and separate open/print actions. Database and model work remain on `WorkflowWorker`; Tk only receives frozen event data. Printing opens the chosen HTML with a `?print=1` equivalent local print-view marker or a generated print wrapper; automatic-dialog failure leaves the in-page print button available.

- [ ] **Step 4: Run UI and worker regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\ui\test_learning_center.py tests\ui\test_view_model.py tests\review\test_review_ui.py -q`

Expected: PASS, including maximize/restore and existing footer behavior.

- [ ] **Step 5: Commit the UI slice**

```powershell
git add src/qingzi_learning/ui/learning_center.py src/qingzi_learning/ui/app.py src/qingzi_learning/workflow/controller.py tests/ui/test_learning_center.py tests/ui/test_view_model.py
git commit -m "feat: add learning report center"
```

### Task 6: Verify, build, install, and inspect real-data output

**Files:**
- Modify: `docs/verification/learning-reports-acceptance.md`

**Interfaces:**
- Consumes all report tasks.
- Produces an installed executable and a real report snapshot without changing historical question facts.

- [ ] **Step 1: Run focused and full automated verification**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting tests\storage\test_report_repository.py tests\ui\test_learning_center.py -q`

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass except the existing Windows symlink privilege skip.

- [ ] **Step 2: Build and smoke the packaged executable**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipTests`

Run: `dist\晴子学习助手\晴子学习助手.exe --smoke-check`

Expected: exit code `0`; packaged report narrative schema and storage schema are present.

- [ ] **Step 3: Generate a report against a copied production database**

Copy the live SQLite database to a temporary acceptance directory, point an `AppConfig` copy at a temporary knowledge root, invoke `LearningReportService.generate`, and verify: two report HTML files plus manifest exist, three subject sections are present, sample counts match the copied DB, no English internal enum is visible, and the child report contains no parent-only evidence table.

- [ ] **Step 4: Inspect rendered A4 pages and print routes**

Open both HTML files in the default browser, inspect print preview at A4 portrait, and record page count, clipped content, answer/parent-detail isolation, and whether the system print dialog appears. Do not send a physical print job during automated acceptance.

- [ ] **Step 5: Install only after the running app has exited**

Use `scripts\install_desktop_shortcut.ps1` with the existing install root and desktop path. Preserve `C:\Users\Home\Documents\QingziLearningAssistant\assets\qingzi-photo.ico`, restore the shortcut's `IconLocation`, compare installed and built SHA-256 hashes, and run the installed `--smoke-check`.

- [ ] **Step 6: Record evidence and commit**

Write exact test counts, hashes, real-output paths, print-preview observations, and any explicit hardware limitations into `docs/verification/learning-reports-acceptance.md`.

```powershell
git add docs/verification/learning-reports-acceptance.md
git commit -m "docs: verify incremental learning reports"
```
