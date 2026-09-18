# Targeted Practice Exams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate parent-approved, printable targeted practice exams from confirmed learning evidence and connect scanned answers back as retest evidence.

**Architecture:** A deterministic blueprint builder selects target knowledge points and allocations from the learning profile. A schema-constrained Codex generator drafts new questions, a separate verifier pass and local validator gate publication, and immutable SQLite exam records own every printable artifact. Printed exam and question IDs are recognized during the normal capture flow, validated locally, and recorded as idempotent retest links.

**Tech Stack:** Python 3.13, SQLite, Tkinter, JSON Schema, Codex CLI, self-contained HTML/CSS, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-17-learning-reports-and-targeted-exams-design.md`

## Global Constraints

- Subjects remain exactly `语文`, `数学`, and `英语`; generated content is for grade 5.
- Default allocation is approximately `50%` primary weaknesses, `30%` related points, and `20%` stable points using deterministic integer allocation.
- `needs_review`, low-confidence model facts below `0.80`, out-of-scope facts, and missing evidence cannot influence targeting.
- Generated questions must be new questions, not copies of original mistakes.
- A draft must pass local validation, one verifier pass, and explicit parent approval before printing.
- Student paper, answer sheet, answer explanations, and blueprint notes are independent files and independent print actions.
- Student HTML contains no answers, scoring rubric, hidden answer JSON, or links to answer files.
- Unknown or forged exam IDs never update retest statistics automatically.
- All model calls remain read-only and receive bounded, de-identified context.
- Existing ordinary homework analysis remains backward compatible.

---

### Task 1: Persist exam drafts, questions, approvals, and attempts

**Files:**
- Modify: `src/qingzi_learning/storage/schema.sql`
- Modify: `src/qingzi_learning/storage/repository.py`
- Test: `tests/storage/test_exam_repository.py`

**Interfaces:**
- Produces: frozen dataclasses `ExamRun`, `ExamQuestion`, and `ExamAttempt`.
- Produces: `create_exam_run(exam_id, subject, request, blueprint) -> ExamRun`.
- Produces: `save_exam_generation(exam_id, generation, verification, questions) -> ExamRun`.
- Produces: `approve_exam(exam_id, output_files, *, expected_revision: int) -> ExamRun`.
- Produces: `fail_exam(exam_id, error_code) -> ExamRun`, `get_exam_run(exam_id)`, `list_exam_runs()`, `exam_questions(exam_id)`, and `record_exam_attempt(...) -> bool`.

- [ ] **Step 1: Write lifecycle and idempotency tests**

```python
def test_exam_cannot_be_approved_before_validated_questions(repo):
    run = repo.create_exam_run("QZ-MATH-1", "数学", REQUEST, BLUEPRINT)
    with pytest.raises(ValueError, match="尚未通过校验"):
        repo.approve_exam(run.exam_id, {}, expected_revision=run.revision)

def test_exam_attempt_is_idempotent(repo, approved_exam, stored_document):
    first = repo.record_exam_attempt(approved_exam.exam_id, "Q01", stored_document, "1", "incorrect")
    second = repo.record_exam_attempt(approved_exam.exam_id, "Q01", stored_document, "1", "incorrect")
    assert first is True and second is False
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\storage\test_exam_repository.py -q`

Expected: missing exam storage API.

- [ ] **Step 3: Add constrained tables and transactional methods**

Create `exam_runs`, `exam_questions`, and `exam_attempts`. Constrain run status to `draft`, `validating`, `needs_parent_approval`, `approved`, or `failed`; use `(exam_id, question_id)` and `(exam_id, exam_question_id, document_id, document_question_id)` uniqueness. Approval must compare revision, require validated questions, and be immutable afterward.

- [ ] **Step 4: Run storage regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\storage\test_exam_repository.py tests\storage\test_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the exam storage slice**

```powershell
git add src/qingzi_learning/storage/schema.sql src/qingzi_learning/storage/repository.py tests/storage/test_exam_repository.py
git commit -m "feat: persist targeted practice exams"
```

### Task 2: Build deterministic targeting blueprints

**Files:**
- Create: `src/qingzi_learning/exams/__init__.py`
- Create: `src/qingzi_learning/exams/blueprint.py`
- Test: `tests/exams/test_blueprint.py`

**Interfaces:**
- Produces: `ExamRequest(subject: str, scope: str, duration_minutes: int, difficulty: str, question_count: int, include_composition: bool, include_reading: bool)`.
- Produces: `allocate_question_counts(total: int, available: dict[str, bool]) -> dict[str, int]`.
- Produces: `BlueprintBuilder(repo).build(request: ExamRequest) -> dict`.

- [ ] **Step 1: Write exact allocation and evidence-filter tests**

```python
@pytest.mark.parametrize(("total", "expected"), [
    (5, {"primary": 3, "related": 1, "stable": 1}),
    (10, {"primary": 5, "related": 3, "stable": 2}),
    (11, {"primary": 6, "related": 3, "stable": 2}),
])
def test_default_allocation_is_deterministic(total, expected):
    assert allocate_question_counts(total, {"primary": True, "related": True, "stable": True}) == expected

def test_blueprint_excludes_pending_low_confidence_and_other_subjects(repo, evidence_fixture):
    plan = BlueprintBuilder(repo).build(ExamRequest("数学", "分数", 40, "适中", 10, False, False))
    assert all(item["subject"] == "数学" for item in plan["targets"])
    assert "待确认知识点" not in {item["knowledge_point"] for item in plan["targets"]}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams\test_blueprint.py -q`

Expected: missing blueprint module.

- [ ] **Step 3: Implement selection, co-occurrence relations, and shortage disclosure**

Use current report evidence rows: primary points sort by review priority and recency; related points come from knowledge points co-occurring on a confirmed question/document or sharing a recurring error category; stable points require positive effective attempts and no pending review. Apply case-insensitive scope-token matching to point names and representative prompts. When a bucket is unavailable, reallocate to primary then related, and record the final allocation plus a Chinese disclosure string.

- [ ] **Step 4: Run blueprint and profile tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams\test_blueprint.py tests\reporting\test_profile.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the blueprint slice**

```powershell
git add src/qingzi_learning/exams tests/exams/test_blueprint.py
git commit -m "feat: build targeted exam blueprints"
```

### Task 3: Generate, verify, and repair structured exams

**Files:**
- Create: `src/qingzi_learning/schema/exam-generation.schema.json`
- Create: `src/qingzi_learning/schema/exam-verification.schema.json`
- Create: `src/qingzi_learning/exams/generator.py`
- Create: `src/qingzi_learning/exams/validation.py`
- Create: `src/qingzi_learning/exams/service.py`
- Modify: `pyproject.toml`
- Modify: `scripts/build.ps1`
- Modify: `tests/test_packaging_assets.py`
- Test: `tests/exams/test_generator.py`
- Test: `tests/exams/test_validation.py`

**Interfaces:**
- Produces: `ExamGenerator.generate(exam_id: str, request: ExamRequest, blueprint: dict) -> dict`.
- Produces: `ExamVerifier.verify(request: ExamRequest, blueprint: dict, generation: dict) -> dict`.
- Produces: `validate_exam(request, blueprint, generation) -> tuple[ExamQuestion, ...]`.
- Produces: `TargetedExamService.create_draft(request: ExamRequest, *, now: datetime | None = None) -> ExamRun` with at most one repair attempt.

- [ ] **Step 1: Write malicious, duplicate, scoring, leakage, and repair tests**

```python
def test_validator_rejects_answer_leakage_and_unknown_targets(request, blueprint):
    generation = valid_generation()
    generation["questions"][0]["prompt"] = "答案：3/4，请计算……"
    generation["questions"][0]["knowledge_points"] = ["不存在"]
    with pytest.raises(ExamValidationError):
        validate_exam(request, blueprint, generation)

def test_service_repairs_once_then_requires_parent_approval(service):
    run = service.create_draft(REQUEST, now=NOW)
    assert service.generator.calls == 2
    assert run.status == "needs_parent_approval"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams\test_generator.py tests\exams\test_validation.py tests\test_packaging_assets.py -q`

Expected: missing modules and schemas.

- [ ] **Step 3: Implement two read-only Codex contracts and local gates**

The generator receives only request, blueprint targets, bounded prompt summaries, and reference-answer patterns. Require stable `Q01`… IDs, total 100 points, targeted knowledge IDs, answer, explanation, and rubric. The verifier returns per-question verdicts and a safe repaired generation when required. Local validation checks exact question count, unique IDs, points totaling 100, known targets, subject/range binding, non-empty answer/rubric, normalized prompt uniqueness, safe text, and absence of answer markers in student prompts. Permit exactly one repair generation, then fail closed.

- [ ] **Step 4: Package schemas and run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams tests\test_packaging_assets.py -q`

Expected: PASS; both new schemas appear in packaging checks.

- [ ] **Step 5: Commit the generation slice**

```powershell
git add src/qingzi_learning/schema src/qingzi_learning/exams pyproject.toml scripts/build.ps1 tests/exams tests/test_packaging_assets.py
git commit -m "feat: generate and verify targeted exams"
```

### Task 4: Render, approve, and safely print separate exam artifacts

**Files:**
- Create: `src/qingzi_learning/exams/render.py`
- Modify: `src/qingzi_learning/exams/service.py`
- Modify: `src/qingzi_learning/storage/paths.py`
- Modify: `src/qingzi_learning/export/dashboard.py`
- Test: `tests/exams/test_render.py`
- Test: `tests/exams/test_service.py`
- Modify: `tests/export/test_dashboard.py`

**Interfaces:**
- Produces: `ExamArtifacts(student_path, answer_sheet_path, solutions_path, blueprint_path, manifest_path)`.
- Produces: `KnowledgePaths.exam_directory(year, month, exam_id) -> Path`.
- Produces: `ExamRenderer.render_preview(run, questions) -> str` and `render_approved(run, questions) -> ExamArtifacts`.
- Produces: `TargetedExamService.approve(exam_id: str, *, expected_revision: int) -> ExamArtifacts`.

- [ ] **Step 1: Write answer-isolation and approval-publication tests**

```python
def test_student_artifacts_contain_no_answers_or_hidden_solution_json(renderer, approved_exam):
    artifacts = renderer.render_approved(*approved_exam)
    student = artifacts.student_path.read_text("utf-8")
    answer_sheet = artifacts.answer_sheet_path.read_text("utf-8")
    assert "标准答案" not in student + answer_sheet
    assert approved_exam.questions[0].answer not in student + answer_sheet
    assert "solutions" not in student.lower()

def test_unapproved_exam_cannot_publish(service, draft_exam):
    with pytest.raises(ValueError, match="家长确认"):
        service.approve(draft_exam.exam_id, expected_revision=draft_exam.revision)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams\test_render.py tests\exams\test_service.py -q`

Expected: missing renderer/approval failures.

- [ ] **Step 3: Implement preview plus five guarded outputs**

Preview can show questions and solutions to the parent but is not printable as an approved student paper. Approval stages `学生试卷.html`, `答题纸.html`, `答案与解析.html`, `组卷说明.html`, and `exam.json`, validates hashes, publishes atomically, then changes status to `approved`. Every page prints exam ID and stable question IDs. Add A4 CSS and one in-page `window.print()` button per HTML. Dashboard links only to approved runs.

- [ ] **Step 4: Run exam render and dashboard tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams tests\export\test_dashboard.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the artifact slice**

```powershell
git add src/qingzi_learning/exams src/qingzi_learning/storage/paths.py src/qingzi_learning/export/dashboard.py tests/exams tests/export/test_dashboard.py
git commit -m "feat: publish printable practice exams"
```

### Task 5: Extend the learning center with exam generation and approval

**Files:**
- Modify: `src/qingzi_learning/ui/learning_center.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `src/qingzi_learning/workflow/controller.py`
- Test: `tests/ui/test_learning_center_exam.py`
- Modify: `tests/ui/test_view_model.py`

**Interfaces:**
- Produces controller methods `preview_exam_blueprint(request)`, `generate_exam(request)`, `approve_exam(exam_id, expected_revision)`, and `exam_history()`.
- Extends worker commands with `preview_exam_blueprint`, `generate_exam`, `approve_exam`, and `list_exams`.
- Extends learning-center events with immutable blueprint, exam run, question preview, and artifact paths.

- [ ] **Step 1: Write form-validation, worker, and print-target tests**

```python
def test_exam_form_rejects_invalid_duration_and_question_count(form):
    form.duration.set("0")
    form.question_count.set("101")
    assert form.build_request() is None
    assert "时长" in form.error_text.get() and "题量" in form.error_text.get()

def test_approved_exam_has_four_separate_print_targets(dialog, approved_exam_event):
    dialog.handle(approved_exam_event)
    assert {path.name for path in dialog.exam_print_paths} == {
        "学生试卷.html", "答题纸.html", "答案与解析.html", "组卷说明.html"
    }
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\ui\test_learning_center_exam.py tests\ui\test_view_model.py -q`

Expected: missing exam tab/commands.

- [ ] **Step 3: Implement the exam tab without blocking Tk**

Provide subject, scope, duration, difficulty, count, composition, and reading controls; require blueprint preview before generation; show why each target is selected; disable approval until validation completes; show full parent preview; and keep four print buttons disabled until approval. All database/model work stays in the worker. Closing and reopening reloads draft/approval history from SQLite.

- [ ] **Step 4: Run UI regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\ui\test_learning_center.py tests\ui\test_learning_center_exam.py tests\ui\test_view_model.py -q`

Expected: PASS with report tab unchanged.

- [ ] **Step 5: Commit the exam UI slice**

```powershell
git add src/qingzi_learning/ui/learning_center.py src/qingzi_learning/ui/app.py src/qingzi_learning/workflow/controller.py tests/ui/test_learning_center_exam.py tests/ui/test_view_model.py
git commit -m "feat: add targeted exam workflow to learning center"
```

### Task 6: Recognize approved exam retests during normal capture

**Files:**
- Modify: `src/qingzi_learning/domain.py`
- Modify: `src/qingzi_learning/schema/analysis-result.schema.json`
- Modify: `src/qingzi_learning/schema/analysis-transport.schema.json`
- Modify: `src/qingzi_learning/analysis/codex_cli.py`
- Modify: `src/qingzi_learning/storage/repository.py`
- Modify: `src/qingzi_learning/knowledge/updater.py`
- Test: `tests/test_analysis_schema.py`
- Test: `tests/exams/test_retest_linking.py`
- Modify: `tests/e2e/test_three_subject_workflow.py`
- Modify: `tests/e2e/test_mixed_subject_workflow.py`

**Interfaces:**
- Extends `AnalysisResult` with `source_exam_id: str | None`.
- Extends `QuestionAnalysis` with `source_exam_question_id: str | None`.
- Produces: `KnowledgeRepository.link_exam_attempts(analysis: AnalysisResult) -> tuple[ExamAttempt, ...]`.

- [ ] **Step 1: Write backward-compatibility and forged-ID tests**

```python
def test_normal_homework_requires_explicit_null_exam_fields(valid_payload):
    valid_payload["source_exam_id"] = None
    for question in valid_payload["questions"]:
        question["source_exam_question_id"] = None
    validate_analysis_payload(valid_payload)

def test_unknown_exam_id_does_not_create_attempt_and_requires_review(repo, approved_exam, analysis_factory):
    analysis = analysis_factory(source_exam_id="QZ-FAKE", source_exam_question_id="Q01")
    result = repo.link_exam_attempts(analysis)
    assert result == ()
    assert repo.get_document(analysis.document_id)["questions"][0]["status"] == "needs_review"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_analysis_schema.py tests\exams\test_retest_linking.py -q`

Expected: schema/domain missing fields.

- [ ] **Step 3: Add nullable IDs, prompting, and local validation**

Require both fields in new model responses with `null` for ordinary work. Prompt the analyzer to copy only visibly printed `QZ-...` and `Q..` identifiers. Before knowledge application, verify approved run, subject, and question membership. A full valid match records attempts transactionally. Unknown, partial, duplicated, or cross-subject matches force affected questions to `needs_review`; they cannot affect mastery until a parent resolves them.

- [ ] **Step 4: Run schema, workflow, and mixed-subject regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_analysis_schema.py tests\exams\test_retest_linking.py tests\e2e -q`

Expected: PASS for ordinary documents (`null` fields), approved retests, and mixed-subject splitting.

- [ ] **Step 5: Commit the retest slice**

```powershell
git add src/qingzi_learning/domain.py src/qingzi_learning/schema src/qingzi_learning/analysis/codex_cli.py src/qingzi_learning/storage/repository.py src/qingzi_learning/knowledge/updater.py tests/test_analysis_schema.py tests/exams/test_retest_linking.py tests/e2e
git commit -m "feat: link scanned practice exam retests"
```

### Task 7: Surface retest outcomes in subsequent reports

**Files:**
- Modify: `src/qingzi_learning/reporting/profile.py`
- Modify: `src/qingzi_learning/reporting/render.py`
- Modify: `src/qingzi_learning/reporting/narrative.py`
- Test: `tests/reporting/test_retest_progress.py`

**Interfaces:**
- Extends profile snapshots with `retests` containing exam ID, attempt count, result changes, and target knowledge points.
- Child reports show concise progress only when evidence exists.
- Parent reports show exam and question traceability.

- [ ] **Step 1: Write cross-report retest tests**

```python
def test_next_report_describes_retest_change_without_erasing_original_error(history_with_retest):
    first, second = history_with_retest.generate_two_reports()
    assert second["retests"][0]["previous_status"] == "incorrect"
    assert second["retests"][0]["current_status"] == "correct"
    assert second["subjects"]["数学"]["knowledge_points"]["分数应用"]["incorrect"] >= 1
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting\test_retest_progress.py -q`

Expected: missing retest profile data.

- [ ] **Step 3: Add retest facts and grounded wording**

Read `exam_attempts` joined to immutable exam targets and effective questions. Compare report snapshots rather than deleting old errors. Child wording uses “这次复测已答对，建议再确认一次” until repeated evidence supports stability; parent view links exam ID, printed question ID, capture document, and original target.

- [ ] **Step 4: Run all reporting and exam tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\reporting tests\exams -q`

Expected: PASS.

- [ ] **Step 5: Commit the reporting closure slice**

```powershell
git add src/qingzi_learning/reporting tests/reporting/test_retest_progress.py
git commit -m "feat: report practice exam retest progress"
```

### Task 8: Full verification, build, install, and real closed-loop acceptance

**Files:**
- Create: `docs/verification/targeted-exams-acceptance.md`

**Interfaces:**
- Consumes the complete exam and report workflow.
- Produces an installed executable and recorded acceptance evidence.

- [ ] **Step 1: Run focused and complete test suites**

Run: `.\.venv\Scripts\python.exe -m pytest tests\exams tests\reporting tests\ui\test_learning_center_exam.py tests\e2e -q`

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass except the known Windows symlink privilege skip.

- [ ] **Step 2: Build and smoke the packaged app**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipTests`

Run: `dist\晴子学习助手\晴子学习助手.exe --smoke-check`

Expected: exit code `0`; all analysis, report, exam-generation, exam-verification, and storage schemas are packaged.

- [ ] **Step 3: Generate a real-data draft without printing**

Use a copy of the production database and a temporary knowledge root. Generate a small mathematics exam from real confirmed weaknesses, inspect the parent preview, approve it in the acceptance copy, and verify the five artifact hashes and answer isolation. Do not treat generated academic content as production-ready until the parent explicitly approves it in the installed UI.

- [ ] **Step 4: Verify print previews**

Open student paper, answer sheet, solutions, and blueprint notes separately in print preview. Confirm A4 pagination, question IDs, exam ID, answer separation, and that each button launches only its named file. Do not send a physical print job automatically.

- [ ] **Step 5: Exercise retest linking with controlled fixtures**

Analyze a fixture image carrying the accepted exam and question IDs; verify one `exam_attempts` row and one mastery update. Replay the same analysis and verify no duplicate. Analyze forged and cross-subject IDs and verify `needs_review` with no attempt row.

- [ ] **Step 6: Install safely after checking the process is closed**

Use the rollback-safe installer, preserve the custom photo icon, compare built and installed SHA-256, run installed smoke check, and rebuild the live knowledge homepage. Never terminate an active user capture process to install.

- [ ] **Step 7: Record acceptance evidence and commit**

Document exact tests, hashes, generated acceptance paths, print-preview observations, closed-loop database rows, and any manual-review limitations.

```powershell
git add docs/verification/targeted-exams-acceptance.md
git commit -m "docs: verify targeted practice exam workflow"
```
