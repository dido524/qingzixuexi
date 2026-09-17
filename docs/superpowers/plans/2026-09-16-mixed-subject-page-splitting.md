# 混合学科按页拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次分析整批作业后，按页面学科自动拆成独立文档；仅在页面模糊时用大图窗口逐页确认，并在程序位于后台时闪烁任务栏图标。

**Architecture:** 保留现有父工作流作为权威编排记录，在领域层加入逐页学科判断，由纯函数生成确定性拆分计划。所有人工选择完成后，控制器先完成受保护的文件暂存，再用单个 SQLite 事务写入全部子文档和知识事实，最后借助现有 PublicationCoordinator 一次发布完整知识库；旧缓存继续走原有整批确认分支。

**Tech Stack:** Python 3.12、dataclasses、JSON Schema、SQLite、Tkinter/Pillow、ctypes Win32 API、pytest、现有 PublicationCoordinator 与安全文件写入工具。

**Spec:** `docs/superpowers/specs/2026-09-16-mixed-subject-page-splitting-design.md`

## Global Constraints

- 最终归类单位是整页，不按题目拆分，也不为每页单独调用模型。
- 新分析必须为每个捕获页提供且仅提供一个 `page_subjects` 条目；页码、学科、置信度和完整性均由本地再次校验。
- 置信度低于 `AppConfig.subject_confidence_threshold` 时，无论模型标记如何都必须人工确认。
- 旧缓存缺少 `page_subjects` 时不重新调用模型，继续使用旧的整批科目确认。
- 单学科批次保留原文档编号；混合批次使用 `<parent>--chinese`、`<parent>--math`、`<parent>--english`。
- 所有待确认页完成前，不创建部分学科文档、不写入知识统计、不清理原始暂存资产。
- 所有子文档、题目和知识统计在一个 SQLite 事务中更新；发布仍由 PublicationCoordinator 进行全量原子发布。
- 人工确认是最终权威值；模型建议和人工覆盖均保留在父任务负载中以供审计和恢复。
- 页面文件继续使用固定根目录、路径守卫、重解析点检查、SHA-256 校验和原子替换。
- Windows 闪烁只提示任务栏，不抢占焦点；非 Windows 环境必须安全无操作。
- 页面确认窗口约占可用工作区 85%，在 1024×700、最大化和恢复后底部按钮始终可见。

---

## File map

- `src/qingzi_learning/domain.py`: 页面学科领域对象和分析结果兼容字段。
- `src/qingzi_learning/schema/analysis-result.schema.json`: 大模型逐页学科响应契约。
- `src/qingzi_learning/analysis/codex_cli.py`: 提示词、解析和捕获页集合校验。
- `src/qingzi_learning/workflow/subject_split.py`: 纯函数式页面分配、稳定子文档编号和分析拆分。
- `src/qingzi_learning/workflow/controller.py`: 父任务编排、逐页确认、文件暂存、恢复与批量发布。
- `src/qingzi_learning/storage/repository.py`: 子任务与全部知识事实的单事务写入。
- `src/qingzi_learning/knowledge/updater.py`: 批量分析更新入口。
- `src/qingzi_learning/ui/page_subject_dialog.py`: 大图逐页确认窗口。
- `src/qingzi_learning/ui/taskbar.py`: 可注入的 Windows 任务栏闪烁封装。
- `src/qingzi_learning/ui/app.py`: worker 命令、视图状态、确认窗口和通知器集成。
- `tests/`: 每个边界对应的单元、工作流、UI 与端到端回归测试。

### Task 1: 扩展分析结果契约

**Files:**
- Modify: `src/qingzi_learning/domain.py`
- Modify: `src/qingzi_learning/schema/analysis-result.schema.json`
- Modify: `src/qingzi_learning/analysis/codex_cli.py`
- Modify: `tests/test_analysis_schema.py`
- Modify: `tests/analysis/test_codex_cli.py`

**Interfaces:**
- Produces: `PageSubjectAssignment(page: int, suggested_subject: Subject, confidence: float, needs_confirmation: bool, reason: str)`。
- Produces: `AnalysisResult.page_subjects: tuple[PageSubjectAssignment, ...] = ()`；空元组只代表旧缓存兼容。
- Produces: `_from_payload(payload: dict, *, allow_legacy: bool = False) -> AnalysisResult`；只有读取已持久化的旧任务时传 `allow_legacy=True`。
- Produces: `validate_result(result: AnalysisResult, document: CapturedDocument) -> None` 要求新结果映射非空并执行页集合完全相等校验。

- [ ] **Step 1: 写失败的 Schema 和解析测试**

```python
def test_schema_accepts_complete_page_subjects(valid_payload):
    valid_payload["page_subjects"] = [
        {"page": 1, "suggested_subject": "数学", "confidence": 0.99,
         "needs_confirmation": False, "reason": "计算题"},
        {"page": 2, "suggested_subject": "英语", "confidence": 0.96,
         "needs_confirmation": False, "reason": "英语阅读"},
    ]
    validate_analysis_payload(valid_payload)

@pytest.mark.parametrize("subject", ["科学", "", None])
def test_schema_rejects_invalid_page_subject(subject, valid_payload):
    valid_payload["page_subjects"] = [
        {"page": 1, "suggested_subject": subject, "confidence": 0.9,
         "needs_confirmation": False, "reason": "可辨认"}
    ]
    with pytest.raises(ValidationError):
        validate_analysis_payload(valid_payload)

def test_validate_result_rejects_missing_or_duplicate_page_mapping(document, payload):
    payload["page_subjects"] = [
        {"page": 1, "suggested_subject": "数学", "confidence": 0.9,
         "needs_confirmation": False, "reason": "计算题"},
        {"page": 1, "suggested_subject": "数学", "confidence": 0.9,
         "needs_confirmation": False, "reason": "重复页"},
    ]
    with pytest.raises(ValueError, match="页面学科映射"):
        validate_result(_from_payload(payload), document)

def test_legacy_cached_payload_is_only_accepted_by_explicit_compatibility_path(payload):
    payload.pop("page_subjects", None)
    with pytest.raises(ValidationError):
        _from_payload(payload)
    assert _from_payload(payload, allow_legacy=True).page_subjects == ()
```

- [ ] **Step 2: 运行测试，确认契约尚未实现**

Run: `.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py tests/analysis/test_codex_cli.py -q`

Expected: FAIL，原因是 Schema 不认识 `page_subjects` 或 `AnalysisResult` 没有该属性。

- [ ] **Step 3: 增加领域类型、严格 Schema、提示词和解析**

```python
@dataclass(frozen=True)
class PageSubjectAssignment:
    page: int
    suggested_subject: Subject
    confidence: float
    needs_confirmation: bool
    reason: str

@dataclass(frozen=True)
class AnalysisResult:
    document_id: str
    subject: Subject
    subject_confidence: float
    document_type: str
    grading_mode: GradingMode
    teacher_mark_evidence: tuple[str, ...]
    questions: tuple[QuestionAnalysis, ...]
    summary: str
    page_subjects: tuple[PageSubjectAssignment, ...] = ()
```

在 JSON Schema 顶层 `required` 中加入 `page_subjects`，并定义：

```json
"page_subjects": {
  "type": "array",
  "minItems": 1,
  "items": {
    "type": "object",
    "additionalProperties": false,
    "required": ["page", "suggested_subject", "confidence", "needs_confirmation", "reason"],
    "properties": {
      "page": {"type": "integer", "minimum": 1},
      "suggested_subject": {"enum": ["语文", "数学", "英语"]},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "needs_confirmation": {"type": "boolean"},
      "reason": {"type": "string", "minLength": 1}
    }
  }
}
```

`validate_analysis_payload(payload, allow_legacy=False)` 默认使用上述严格 Schema。仅当 `allow_legacy=True` 且字段确实缺失时，复制内存中的 Schema 并只从顶层 `required` 移除 `page_subjects`，其他字段和 `additionalProperties=false` 约束保持不变。`_from_payload()` 使用相同开关，并把缺失字段解析为空元组。新模型提示词和 adapter 保持默认严格路径。`validate_result()` 对新结果要求映射非空，并检查实际页码集合等于捕获页集合、数组长度等于页数、无重复页码；每道题的 `page` 仍必须属于捕获页集合。

- [ ] **Step 4: 运行分析契约测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py tests/analysis/test_codex_cli.py -q`

Expected: PASS。

- [ ] **Step 5: 提交契约改动**

```powershell
git add src/qingzi_learning/domain.py src/qingzi_learning/schema/analysis-result.schema.json src/qingzi_learning/analysis/codex_cli.py tests/test_analysis_schema.py tests/analysis/test_codex_cli.py
git commit -m "feat: add per-page subject analysis contract"
```

### Task 2: 实现确定性的页面分配与分析拆分

**Files:**
- Create: `src/qingzi_learning/workflow/subject_split.py`
- Create: `tests/workflow/test_subject_split.py`

**Interfaces:**
- Consumes: `AnalysisResult.page_subjects`、`CapturedDocument.pages`、人工覆盖 `Mapping[int, Subject]`。
- Produces: `PendingPageSubject`、`SubjectPageGroup`、`SubjectSplitPlan`。
- Produces: `build_subject_split_plan(document, analysis, overrides, threshold) -> SubjectSplitPlan`。
- Produces: `split_analysis(analysis, plan) -> tuple[AnalysisResult, ...]`。

- [ ] **Step 1: 写稳定编号、低置信度和隔离题目的失败测试**

```python
def test_mixed_batch_gets_stable_subject_children(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)
    assert [(group.subject, group.document_id, group.page_numbers) for group in plan.groups] == [
        (Subject.MATH, f"{document.document_id}--math", (1, 2)),
        (Subject.ENGLISH, f"{document.document_id}--english", (3,)),
    ]

def test_low_confidence_forces_confirmation_even_when_model_says_false(document, analysis):
    analysis = replace(analysis, page_subjects=(
        PageSubjectAssignment(1, Subject.MATH, 0.79, False, "数字较模糊"),
    ))
    plan = build_subject_split_plan(document, analysis, {}, 0.80)
    assert tuple(item.page for item in plan.unresolved_pages) == (1,)

def test_split_analysis_keeps_questions_with_their_pages(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)
    children = split_analysis(analysis, plan)
    math, english = children
    assert {q.page for q in math.questions} == {1, 2}
    assert {q.page for q in english.questions} == {3}
    assert math.document_id.endswith("--math")
    assert english.document_id.endswith("--english")
```

- [ ] **Step 2: 运行新测试确认模块不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_subject_split.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现纯领域拆分模块**

```python
SUBJECT_SUFFIX = {
    Subject.CHINESE: "chinese",
    Subject.MATH: "math",
    Subject.ENGLISH: "english",
}

@dataclass(frozen=True)
class PendingPageSubject:
    page: int
    image_path: Path
    suggested_subject: Subject
    confidence: float
    reason: str

@dataclass(frozen=True)
class SubjectPageGroup:
    subject: Subject
    document_id: str
    page_numbers: tuple[int, ...]

@dataclass(frozen=True)
class SubjectSplitPlan:
    parent_document_id: str
    groups: tuple[SubjectPageGroup, ...]
    unresolved_pages: tuple[PendingPageSubject, ...]
```

`build_subject_split_plan()` 必须先验证页面映射完整且唯一；对 `needs_confirmation` 为真或置信度低于阈值的页面，只在 `overrides` 含该页时采用人工值。若仍有未确认页，`groups` 返回空元组，防止调用方误写部分结果。确认完成后按 `Subject` 枚举顺序生成组：一个学科沿用父编号，多个学科使用稳定后缀。未知覆盖页、遗漏页、重复页和空组抛出 `ValueError`。

`split_analysis()` 只保留组内页的题目；子结果 `subject_confidence` 取该组页面置信度最小值，人工覆盖页按 1.0 计；`teacher_mark_evidence` 原样保留，`summary` 写为 `来自批次 {parent}，共 {page_count} 页、{question_count} 题。`，并把该组的最终页面学科映射写回 `page_subjects`。

- [ ] **Step 4: 运行纯函数测试**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_subject_split.py -q`

Expected: PASS。

- [ ] **Step 5: 提交拆分领域逻辑**

```powershell
git add src/qingzi_learning/workflow/subject_split.py tests/workflow/test_subject_split.py
git commit -m "feat: build deterministic subject split plans"
```

### Task 3: 持久化逐页确认并支持重启恢复

**Files:**
- Modify: `src/qingzi_learning/workflow/controller.py`
- Modify: `tests/workflow/test_controller.py`

**Interfaces:**
- Consumes: Task 2 的 `build_subject_split_plan()`。
- Produces: `WorkflowOutcome.pending_page_subjects: tuple[PendingPageSubject, ...] = ()`。
- Produces: `WorkflowOutcome.child_document_ids: tuple[str, ...] = ()`。
- Produces: `WorkflowController.confirm_page_subject(job_id: str, page_number: int, subject: str) -> WorkflowOutcome`。
- Persists payload keys: `confirmation_mode`、`page_subject_overrides`、`split_plan`、`child_document_ids`、`publish_state`。

- [ ] **Step 1: 写暂停、即时持久化和重启恢复测试**

```python
def test_unclear_page_pauses_before_any_document_is_written(controller, session, repo):
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation"
    assert [item.page for item in outcome.pending_page_subjects] == [2]
    assert repo.get_document(outcome.job_id) is None

def test_page_confirmation_is_persisted_before_next_page(controller, session, repo):
    first = controller.finish_and_analyze(session)
    second = controller.confirm_page_subject(first.job_id, 2, "数学")
    saved = repo.get_job(first.job_id)
    assert saved.payload["page_subject_overrides"] == {"2": "数学"}
    assert [item.page for item in second.pending_page_subjects] == [4]

def test_restarted_controller_uses_cached_analysis_and_remaining_confirmation(
        controller, recreated_controller, session, analyzer):
    first = controller.finish_and_analyze(session)
    controller.confirm_page_subject(first.job_id, 2, "数学")
    recovered = {item.job_id: item for item in recreated_controller.recover_jobs()}[first.job_id]
    assert [item.page for item in recovered.pending_page_subjects] == [4]
    assert analyzer.call_count == 1
```

- [ ] **Step 2: 运行工作流测试确认新 API 缺失**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_controller.py -q`

Expected: FAIL because `confirm_page_subject` and `pending_page_subjects` do not exist。

- [ ] **Step 3: 扩展父任务负载和确认状态机**

新任务 `_stage()` 写入以下初值：

```python
"confirmation_mode": None,
"page_subject_overrides": {},
"split_plan": None,
"child_document_ids": [],
"publish_state": "not_started",
```

分析完成后：若 `analysis.page_subjects` 非空，构建拆分计划；有未确认页则写入 `confirmation_mode="pages"` 和序列化计划，进入既有 `needs_subject_confirmation` 状态。读取 payload 时，只有缺少 `page_subjects` 的已持久化旧分析调用 `_from_payload(payload, allow_legacy=True)`；该结果保持原来的顶层置信度与 `confirm_subject()` 流程，并写入 `confirmation_mode="legacy_batch"`。本次新模型响应始终走默认严格解析，不得借兼容开关接受缺失映射。

```python
def confirm_page_subject(self, job_id: str, page_number: int, subject: str) -> WorkflowOutcome:
    with self._lock:
        job = self._require_job(job_id)
        if job.state != "needs_subject_confirmation" or job.payload.get("confirmation_mode") != "pages":
            raise ValueError("当前任务不等待页面科目确认")
        parsed = Subject(subject)
        pending = {item.page for item in self._page_split_plan(job).unresolved_pages}
        if page_number not in pending:
            raise ValueError("该页面当前不等待确认")
        overrides = dict(job.payload.get("page_subject_overrides", {}))
        overrides[str(page_number)] = parsed.value
        payload = dict(job.payload, page_subject_overrides=overrides)
        saved = self._save(replace(job, payload=payload))
        plan = self._page_split_plan(saved)
        if plan.unresolved_pages:
            return self._outcome(saved)
        saved = self._save(replace(saved, state="pending", payload=self._with_split_plan(payload, plan)))
        return self._resume(saved)
```

`_outcome()` 从权威 payload 和捕获页路径重建 `PendingPageSubject`，绝不信任模型返回的路径。关闭窗口不调用确认 API，因此任务自然保持待确认。`recover_jobs()` 对该状态只生成 outcome，不重新分析。

- [ ] **Step 4: 运行工作流恢复测试**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_controller.py -q`

Expected: PASS，包括旧的 `confirm_subject()` 测试。

- [ ] **Step 5: 提交确认与恢复逻辑**

```powershell
git add src/qingzi_learning/workflow/controller.py tests/workflow/test_controller.py
git commit -m "feat: persist page subject confirmations"
```

### Task 4: 原子写入子文档和知识事实

**Files:**
- Modify: `src/qingzi_learning/storage/repository.py`
- Modify: `src/qingzi_learning/knowledge/updater.py`
- Modify: `src/qingzi_learning/workflow/controller.py`
- Modify: `tests/storage/test_repository.py`
- Modify: `tests/knowledge/test_updater.py`
- Modify: `tests/workflow/test_controller.py`

**Interfaces:**
- Produces: `KnowledgeRepository.apply_split_batch(parent: WorkflowJob, children: tuple[tuple[WorkflowJob, CapturedDocument, AnalysisResult], ...], *, now: datetime) -> None`。
- Produces: `KnowledgeUpdater.apply_split_batch(parent: WorkflowJob, children: tuple[tuple[WorkflowJob, CapturedDocument, AnalysisResult], ...], *, now: datetime) -> None`，仅委托仓库同名批量事务入口，不逐项提交。
- Controller consumes Task 2 `split_analysis()` and stable child IDs.

- [ ] **Step 1: 写单事务回滚和幂等测试**

```python
def test_apply_split_batch_rolls_back_all_children(repo, parent_job, child_rows, monkeypatch):
    original = repo._save_analysis
    calls = 0
    def fail_second(analysis):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected")
        original(analysis)
    monkeypatch.setattr(repo, "_save_analysis", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        repo.apply_split_batch(parent_job, child_rows, now=NOW)
    assert repo.get_document(child_rows[0][0].job_id) is None
    assert repo.get_document(child_rows[1][0].job_id) is None

def test_retrying_same_split_batch_does_not_duplicate_knowledge(repo, parent_job, child_rows):
    repo.apply_split_batch(parent_job, child_rows, now=NOW)
    before = repo.dashboard_snapshot()
    repo.apply_split_batch(parent_job, child_rows, now=NOW)
    assert repo.dashboard_snapshot() == before
```

- [ ] **Step 2: 运行仓库与 updater 测试确认接口缺失**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_repository.py tests/knowledge/test_updater.py tests/workflow/test_controller.py -q`

Expected: FAIL because `apply_split_batch` is absent。

- [ ] **Step 3: 实现批量事务与安全文件暂存**

仓库方法在一个 `with self.connection:` 块中执行：验证父任务当前快照；为每个 child upsert `documents`、`pages`、`processing_jobs`、`workflow_jobs`；调用 `_save_analysis()`；收集旧、新知识点并统一重算；把 child 的 `knowledge_applied` 设为真；把 parent 的 `child_document_ids`、`split_plan`、`publish_state="facts_applied"` 和 `knowledge_applied=True` 一并写入。任何异常由 SQLite 自动回滚。

Controller 新增 `_stage_split_files(job, plan) -> tuple[CapturedDocument, ...]`：

1. 从父 payload 的 `pages` 获取源路径和 SHA-256。
2. 对每个子组建立知识库根目录下的 guarded 暂存目录，保持 `page_005.jpg` 这类原文件名和原页码。
3. 复制后重新计算哈希；全部文件验证成功后原子写入 `split-manifest.json`，内容包含父编号、child 编号、最终页分配、模型建议、人工覆盖和源哈希。
4. 文件已存在时验证哈希后复用；哈希不符则停止，不覆盖未知内容。
5. 文件准备完毕后由 Controller 调用一次 `self.updater.apply_split_batch(parent, children, now=self._now())`；updater 再调用仓库同名方法，事务成功前不删除 spool 文件。

每个 child workflow payload 保存 `parent_job_id`、`archive_kind="raw"`、自己的 pages/assets 和 `mirror_path`。父任务仍使用 spool 中的 `analysis_state.json`；child 镜像位于各自 guarded raw 目录，避免共享路径互相覆盖。

- [ ] **Step 4: 运行批量事务和工作流测试**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_repository.py tests/knowledge/test_updater.py tests/workflow/test_controller.py -q`

Expected: PASS；故障注入后两个 child 均不存在，重试后统计只计一次。

- [ ] **Step 5: 提交原子拆分持久化**

```powershell
git add src/qingzi_learning/storage/repository.py src/qingzi_learning/knowledge/updater.py src/qingzi_learning/workflow/controller.py tests/storage/test_repository.py tests/knowledge/test_updater.py tests/workflow/test_controller.py
git commit -m "feat: commit split documents atomically"
```

### Task 5: 批量发布、子任务复核和清理时序

**Files:**
- Modify: `src/qingzi_learning/workflow/controller.py`
- Modify: `src/qingzi_learning/review/service.py`
- Modify: `tests/workflow/test_controller.py`
- Modify: `tests/review/test_publication_completeness.py`
- Modify: `tests/review/test_review_service.py`

**Interfaces:**
- Consumes: Task 4 已持久化的 child workflow rows and documents。
- Produces: parent `WorkflowOutcome.child_document_ids` and aggregate terminal state。
- Keeps: `ReviewService.confirm_question(child_document_id, ...)` as child-level API。

- [ ] **Step 1: 写多文档发布失败重试与复核隔离测试**

```python
def test_publication_failure_keeps_sources_and_retries_without_duplicate_children(
        controller, session, repo, failing_dashboard):
    first = controller.finish_and_analyze(session)
    assert first.state == "pending"
    assert all(Path(item["source"]).exists() for item in repo.get_job(first.job_id).payload["assets"])
    child_ids = tuple(repo.get_job(first.job_id).payload["child_document_ids"])
    failing_dashboard.enabled = False
    second = controller.retry_pending(first.job_id)
    assert second.child_document_ids == child_ids
    assert second.state in {"completed", "needs_review"}

def test_reviewing_math_child_does_not_change_english_child(review_service, repo, child_ids):
    math_id, english_id = child_ids
    before = repo.get_document(english_id)
    review_service.confirm_question(math_id, "q1", "incorrect", "42", "家长确认")
    assert repo.get_document(english_id) == before
```

- [ ] **Step 2: 运行发布与复核测试**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_controller.py tests/review/test_publication_completeness.py tests/review/test_review_service.py -q`

Expected: FAIL because parent publication does not yet render/finalize children as a batch。

- [ ] **Step 3: 实现全批发布与终态联动**

Parent 进入 `publish_state="pending"` 后创建唯一 `ordinary_publication_id`。传给 `PublicationCoordinator.publish()` 的 `render()` 必须：

```python
def render():
    detail_paths = {
        child_id: str(self.markdown.export_document(child_id))
        for child_id in child_ids
    }
    for subject in sorted({self.repo.get_job(child_id).subject for child_id in child_ids}):
        self.markdown.export_subject(subject)
    dashboard_path = self.dashboard.export()
    return {"analysis_markdown_by_child": detail_paths,
            "dashboard_path": str(dashboard_path)}
```

`finalize(paths)` 在 PublicationCoordinator 已持有的事务中：为每个 child 写入其分析 Markdown 路径并按题目状态设为 `completed` 或 `needs_review`；父任务写入相同 child ID 列表、dashboard 路径、`publish_state="completed"`、`export_pending=False`，终态为 child 状态的聚合（任一待复核则父为 `needs_review`）。发布成功后，控制器逐个验证 child 原图哈希，再按现有 cleanup journal 清理父 spool 资产；清理中断可重试。

ReviewService 继续只对 child 工作流执行复核与重发布；检测 `parent_job_id` 时只更新该 child 的事实，最终全库发布仍由 PublicationCoordinator 保证完整输出。父任务不创建 `documents` 行，所以不会进入知识统计或题目复核列表。

- [ ] **Step 4: 运行发布、复核和完整性测试**

Run: `.venv\Scripts\python.exe -m pytest tests/workflow/test_controller.py tests/review/test_publication_completeness.py tests/review/test_review_service.py -q`

Expected: PASS；发布失败时源图仍存在，成功重试后 child ID 不变。

- [ ] **Step 5: 提交发布编排**

```powershell
git add src/qingzi_learning/workflow/controller.py src/qingzi_learning/review/service.py tests/workflow/test_controller.py tests/review/test_publication_completeness.py tests/review/test_review_service.py
git commit -m "feat: publish split subject batch atomically"
```

### Task 6: 把逐页确认接入 worker 与视图状态

**Files:**
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `tests/ui/test_view_model.py`

**Interfaces:**
- Consumes: `WorkflowOutcome.pending_page_subjects` and `confirm_page_subject()`。
- Extends `_Command.kind` with `confirm_page_subject`；uses existing `page_number` and `subject` fields。
- Produces: `CaptureViewModel.pending_page_subjects`、`page_subject_dialog_needed`、`active_page_subject`。

- [ ] **Step 1: 写 worker 命令与 view-model 状态测试**

```python
def test_worker_routes_page_subject_confirmation(worker, controller):
    operation = worker.submit("confirm_page_subject", job_id="capture-1",
                              page_number=2, subject="英语")
    command = worker._commands.get_nowait()
    worker._process(command)
    controller.confirm_page_subject.assert_called_once_with("capture-1", 2, "英语")
    assert worker.events.get_nowait().operation_id == operation

def test_outcome_opens_first_pending_page_and_advances(view_model, pending_outcome):
    view_model.on_workflow_outcome(pending_outcome)
    assert view_model.page_subject_dialog_needed
    assert view_model.active_page_subject.page == 2
    view_model.on_workflow_outcome(replace(pending_outcome,
        pending_page_subjects=pending_outcome.pending_page_subjects[1:]))
    assert view_model.active_page_subject.page == 4
```

- [ ] **Step 2: 运行 UI 状态测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_view_model.py -q`

Expected: FAIL due to missing command and state fields。

- [ ] **Step 3: 实现命令路由和独立页面确认状态**

在 `WorkflowWorker._process()` 增加：

```python
elif command.kind == "confirm_page_subject":
    outcome = self._controller.confirm_page_subject(
        command.job_id, command.page_number, command.subject)
    self._emit(self._command_event(
        command, "workflow_outcome", outcome=outcome,
        completion=self._completion(outcome)))
```

`CaptureViewModel` 分离旧的 `subject_confirmation_needed` 和新的 `page_subject_dialog_needed`。收到 page-mode outcome 时把待确认集合按 page 排序，当前项为第一项；提交选择期间 `busy=True` 禁用三个按钮；返回仍有待确认页则切到下一页，返回终态则关闭对话框。恢复任务采用同一状态入口，避免两套逻辑分叉。

- [ ] **Step 4: 运行 UI 状态测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_view_model.py -q`

Expected: PASS，并且旧整批科目确认测试保持通过。

- [ ] **Step 5: 提交 worker 与状态机改动**

```powershell
git add src/qingzi_learning/ui/app.py tests/ui/test_view_model.py
git commit -m "feat: route per-page subject confirmations"
```

### Task 7: 实现大图页面学科确认窗口

**Files:**
- Create: `src/qingzi_learning/ui/page_subject_dialog.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Create: `tests/ui/test_page_subject_dialog.py`

**Interfaces:**
- Produces: `PageSubjectDialog(parent, *, on_choose: Callable[[int, str], None], on_defer: Callable[[], None])`。
- Produces: `show(item: PendingPageSubject, index: int, total: int) -> None` and `set_busy(busy: bool) -> None`。
- Consumes: Pillow `Image`/`ImageTk` and the existing pink UI palette/fonts from `app.py`。

- [ ] **Step 1: 写可测试的几何计算与固定底栏测试**

```python
def test_dialog_uses_eighty_five_percent_of_work_area():
    assert dialog_geometry(1920, 1080) == (1632, 918)
    assert dialog_geometry(1024, 700) == (870, 595)

def test_layout_keeps_footer_inside_small_window(tk_root):
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: None,
                               on_defer=lambda: None)
    dialog.window.geometry("870x595")
    dialog.window.update_idletasks()
    assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.window.winfo_height()
    assert all(button.winfo_viewable() for button in dialog.subject_buttons)
```

- [ ] **Step 2: 在可用图形环境运行窗口测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_page_subject_dialog.py -q`

Expected: FAIL because the module does not exist. 若当前会话无 Tk display，fixture 只跳过真实窗口测试，纯 geometry 测试仍必须失败并随后通过。

- [ ] **Step 3: 实现响应式大图窗口并接入 app**

```python
def dialog_geometry(screen_width: int, screen_height: int) -> tuple[int, int]:
    return max(720, int(screen_width * .85)), max(520, int(screen_height * .85))
```

窗口使用三行 grid：header `weight=0`、image frame `weight=1`、footer `weight=0`；图片 canvas 使用 `sticky="nsew"`，footer 使用 `sticky="ew"`，从而最大化或恢复时按钮不被挤出。`<Configure>` 事件做 120ms `after_cancel/after` 去抖，按 canvas 当前大小用 `Image.Resampling.LANCZOS` 保持比例缩放，并保留 `PhotoImage` 强引用。

Header 显示 `第 {index}/{total} 张待确认页面`、模型建议、百分比置信度和 reason。Footer 固定三个同等样式按钮“语文 / 数学 / 英语”；选择时立即 `set_busy(True)` 并调用 `on_choose(item.page, subject)`。WM_DELETE_WINDOW 只调用 `on_defer()` 并隐藏窗口，不改变任务状态。最后一页 outcome 返回终态后由 app 销毁窗口，不要求额外完成操作。

- [ ] **Step 4: 运行窗口与现有 UI 测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_page_subject_dialog.py tests/ui/test_view_model.py -q`

Expected: PASS；真实 Tk 测试同时执行 1024×700、1920×1080、zoomed 后 normal 三种布局断言。

- [ ] **Step 5: 提交大确认窗口**

```powershell
git add src/qingzi_learning/ui/page_subject_dialog.py src/qingzi_learning/ui/app.py tests/ui/test_page_subject_dialog.py tests/ui/test_view_model.py
git commit -m "feat: add large page subject confirmation dialog"
```

### Task 8: 实现 Windows 任务栏闪烁提醒

**Files:**
- Create: `src/qingzi_learning/ui/taskbar.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Create: `tests/ui/test_taskbar.py`
- Modify: `tests/ui/test_view_model.py`

**Interfaces:**
- Produces: `TaskbarNotifier(api=None, platform: str = sys.platform)`。
- Produces: `flash_if_background(window_handle: int) -> bool` and `stop(window_handle: int) -> None`。
- App keeps a latch keyed by `(job_id, tuple(pending page numbers))` so polling does not repeat the call。

- [ ] **Step 1: 写前台、后台、停止和非 Windows 测试**

```python
def test_background_window_flashes_taskbar_once(fake_api):
    fake_api.foreground = 99
    notifier = TaskbarNotifier(fake_api, platform="win32")
    assert notifier.flash_if_background(42)
    assert fake_api.calls[-1].dwFlags == 0x0000000E

def test_foreground_window_does_not_flash(fake_api):
    fake_api.foreground = 42
    notifier = TaskbarNotifier(fake_api, platform="win32")
    assert not notifier.flash_if_background(42)
    assert fake_api.calls == []

def test_focus_stops_flash(fake_api):
    notifier = TaskbarNotifier(fake_api, platform="win32")
    notifier.stop(42)
    assert fake_api.calls[-1].dwFlags == 0

def test_non_windows_is_noop(fake_api):
    notifier = TaskbarNotifier(fake_api, platform="linux")
    assert not notifier.flash_if_background(42)
    assert fake_api.calls == []
```

- [ ] **Step 2: 运行通知器测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_taskbar.py tests/ui/test_view_model.py -q`

Expected: FAIL because `TaskbarNotifier` does not exist。

- [ ] **Step 3: 实现可注入 Win32 封装并集成焦点事件**

```python
class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD)]

class TaskbarNotifier:
    def flash_if_background(self, window_handle: int) -> bool:
        if self.platform != "win32" or self.api.GetForegroundWindow() == window_handle:
            return False
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), window_handle, 0x0000000E, 0, 0)
        self.api.FlashWindowEx(ctypes.byref(info))
        return True

    def stop(self, window_handle: int) -> None:
        if self.platform == "win32":
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), window_handle, 0, 0, 0)
            self.api.FlashWindowEx(ctypes.byref(info))
```

在 app 收到新的 page-mode 待确认 outcome 时，先比较 latch；只有 latch 变化且 `flash_if_background(root.winfo_id())` 返回真时记录 active flash。主窗口和确认窗口的 `<FocusIn>` 均调用 `stop()` 并清除 active flash；隐藏确认窗口后保留 latch，避免下一次轮询再次闪烁；新的待确认页集合或新的 job 才建立新 latch。该 API 不调用 `SetForegroundWindow`、`focus_force` 或窗口提升函数。

- [ ] **Step 4: 运行通知和 UI 状态测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_taskbar.py tests/ui/test_view_model.py tests/ui/test_page_subject_dialog.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务栏提醒**

```powershell
git add src/qingzi_learning/ui/taskbar.py src/qingzi_learning/ui/app.py tests/ui/test_taskbar.py tests/ui/test_view_model.py
git commit -m "feat: flash taskbar for page confirmation"
```

### Task 9: 端到端验收、旧缓存兼容和构建验证

**Files:**
- Create: `tests/fixtures/mixed_subject_analysis.json`
- Create: `tests/e2e/test_mixed_subject_workflow.py`
- Modify: `tests/e2e/test_three_subject_workflow.py`
- Modify: `docs/verification/v1-acceptance.md`

**Interfaces:**
- Exercises the public flow `finish_and_analyze()` → optional `confirm_page_subject()` → child documents → Markdown/dashboard publication。
- Preserves legacy `confirm_subject()` and all existing single-subject fixture behavior。

- [ ] **Step 1: 添加六页自动拆分验收 fixture 和失败测试**

Fixture 中第 1–4 页为数学，第 5–6 页为英语，全部 `confidence >= 0.95` 且 `needs_confirmation=false`。测试断言：

```python
def test_six_page_batch_auto_splits_math_and_english(app_harness):
    outcome = app_harness.finish_fixture("mixed_subject_analysis.json")
    assert outcome.state in {"completed", "needs_review"}
    assert outcome.child_document_ids == (
        f"{outcome.job_id}--math", f"{outcome.job_id}--english")
    math = app_harness.repo.get_document(outcome.child_document_ids[0])
    english = app_harness.repo.get_document(outcome.child_document_ids[1])
    assert [page["page_number"] for page in math["pages"]] == [1, 2, 3, 4]
    assert [page["page_number"] for page in english["pages"]] == [5, 6]
    assert all(q["page"] <= 4 for q in math["questions"])
    assert all(q["page"] >= 5 for q in english["questions"])
    assert "英语" not in {point for q in math["questions"] for point in q["knowledge_points"]}
```

再增加一个旧 fixture 不含 `page_subjects`，断言它仍进入 legacy batch confirmation，且 analyzer 只调用一次。

- [ ] **Step 2: 运行新端到端测试**

Run: `.venv\Scripts\python.exe -m pytest tests/e2e/test_mixed_subject_workflow.py tests/e2e/test_three_subject_workflow.py -q`

Expected: 新验收在最终集成完成前失败；旧三学科测试保持通过。

- [ ] **Step 3: 修正集成边界并记录验收步骤**

只修改端到端测试暴露的具体集成缺口。`docs/verification/v1-acceptance.md` 增加人工检查：连续拍 6 页混合资料、观察无需整批选科；制造一页低置信度、确认窗口大图和固定按钮；切到其他窗口后触发待确认并观察任务栏闪烁；确认后重启应用验证不重复分析；打开数学/英语知识库验证内容隔离。

- [ ] **Step 4: 运行分层与全量测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py tests/analysis/test_codex_cli.py tests/workflow/test_subject_split.py tests/workflow/test_controller.py tests/storage/test_repository.py tests/knowledge/test_updater.py tests/ui/test_view_model.py tests/ui/test_page_subject_dialog.py tests/ui/test_taskbar.py tests/e2e/test_mixed_subject_workflow.py -q`

Expected: PASS。

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: 全量 PASS，无失败或意外跳过；仅无图形环境时允许已注明的 Tk 真实窗口测试跳过。

- [ ] **Step 5: 构建桌面程序并做启动烟雾测试**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1`

Expected: 生成新的可执行文件/分发目录，命令退出码为 0。

Run: `& 'dist\晴子学习助手\晴子学习助手.exe' --smoke-check`

Expected: 进程在 20 秒内退出且退出码为 0；打包后的 Schema、SQLite DDL、Pillow、Tkinter 和任务栏模块均可导入。

- [ ] **Step 6: 提交端到端验收与文档**

```powershell
git add tests/fixtures/mixed_subject_analysis.json tests/e2e/test_mixed_subject_workflow.py tests/e2e/test_three_subject_workflow.py docs/verification/v1-acceptance.md
git commit -m "test: verify mixed subject page splitting"
```

### Task 10: 最终差异审查和现场验收

**Files:**
- Inspect: all files changed since `8935f1d`
- Update only if verification finds a concrete defect.

**Interfaces:**
- Confirms every requirement in the spec is implemented and externally observable。

- [ ] **Step 1: 审查改动范围与安全属性**

Run: `git diff --stat 8935f1d..HEAD`

Run: `git diff --check 8935f1d..HEAD`

Expected: 只包含本功能及其测试/验收文档；`git diff --check` 无输出。

- [ ] **Step 2: 逐项检查关键不变量**

Run: `rg -n "page_subjects|page_subject_overrides|child_document_ids|confirm_page_subject|FlashWindowEx|PageSubjectDialog" src tests docs`

Expected: 分析契约、父任务持久化、child 关联、worker/API、任务栏和大窗口均有实现及测试覆盖。

- [ ] **Step 3: 执行最终全量验证**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: PASS。

- [ ] **Step 4: 按验收文档在本机执行一次真实 UI 检查**

使用固定测试图片或已接入的证件拍照机完成：单学科批次、数学+英语自动拆分、低置信度逐页确认、后台任务栏闪烁、最大化后恢复、确认中关闭并重启恢复。记录实际 child 文档编号、归档目录和知识库首页路径，不使用真实孩子资料提交到远程服务之外的任何位置。

- [ ] **Step 5: 提交验证中发现的修复，或确认工作树干净**

若 Step 1–4 暴露缺陷，为每个独立缺陷先补回归测试、再修复并提交：

```powershell
git add src/qingzi_learning tests docs/verification/v1-acceptance.md
git commit -m "fix: resolve mixed subject acceptance defect"
```

若未发现缺陷，运行 `git status --short`，Expected: 无未提交的本功能改动。
