# 晴子学习助手第一版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 Windows 桌面程序，用指定证件拍照机连续拍摄作业或试卷，安全归档原图，通过当前 ChatGPT 登录的 Codex CLI 完成分科与自适应判卷，并更新可追溯的错题、知识点和本地总览。

**Architecture:** Python 桌面程序把拍摄、分析、结构化存储和阅读层分开。原图先写入可恢复的本机暂存区，Codex 只读分析并返回受 Schema 约束的 JSON，本地代码校验后再写入 SQLite、科目目录、Markdown 和自包含 HTML；任何不确定结论都进入人工确认而不改变长期掌握度。

**Tech Stack:** Python 3.13、Tkinter、OpenCV、cv2-enumerate-cameras、Pillow、SQLite、jsonschema、Codex CLI、pytest、PyInstaller、PowerShell。

**Spec:** `docs/superpowers/specs/2026-09-15-qingzi-learning-assistant-design.md`

## Global Constraints

- Windows 证件拍照机固定为 USB VID `0xBC15`、PID `0x2C1B`；不得自动回退到 `Logi C270 HD WebCam`。
- 知识库根目录固定为 `C:\晴子知识库\5th grade`，科目目录固定为 `语文`、`数学`、`英语`。
- 一次会话至少支持连续拍摄 10 页、重拍当前页和完成后统一分析。
- 原图必须在模型调用前落盘；网络、模型、程序或写入失败不得丢失原图。
- 第一版使用当前 ChatGPT 登录的 Codex CLI，不读取、保存或记录 API Key。
- Codex 必须以只读 sandbox 执行；只有本地已测试代码可以修改知识库。
- 待确认结论不更新长期掌握度；老师明确批改结果优先于自动判卷。
- SQLite/JSON 是结构化事实层，Markdown/HTML 是可重新生成的阅读层。
- 重复处理相同 `document_id` 必须幂等，不重复累计统计。
- 第一版的复习包和掌握度检验只能显示为后续功能。

---

## Planned File Map

```text
qingzi-learning-assistant/
├─ pyproject.toml                         # 包元数据、运行依赖、pytest 配置
├─ requirements-build.txt                 # Windows 打包依赖
├─ src/qingzi_learning/
│  ├─ __init__.py
│  ├─ main.py                             # 组合依赖并启动 Tkinter
│  ├─ config.py                           # 固定路径、硬件 ID、阈值
│  ├─ domain.py                           # 跨模块数据模型和枚举
│  ├─ schema/analysis-result.schema.json  # Codex 输出契约
│  ├─ camera/devices.py                   # 按 VID/PID 枚举并打开设备
│  ├─ camera/quality.py                   # 模糊、亮度、尺寸检查
│  ├─ capture/session.py                  # 页序、重拍、暂存、恢复
│  ├─ analysis/codex_cli.py               # 安全调用 Codex CLI
│  ├─ analysis/service.py                 # Schema 校验与人工确认策略
│  ├─ storage/paths.py                    # 科目目录和安全文件名
│  ├─ storage/repository.py               # SQLite 事务和幂等更新
│  ├─ storage/schema.sql                  # 数据库表与索引
│  ├─ knowledge/updater.py                # 错题与知识点聚合
│  ├─ export/markdown.py                  # Obsidian 兼容 Markdown
│  ├─ export/dashboard.py                 # 自包含知识库首页
│  ├─ workflow/controller.py              # 完成并分析的业务编排
│  └─ ui/app.py                           # 单窗口 UI
├─ tests/                                 # 与 src 结构对应的自动化测试
├─ scripts/build.ps1                      # 创建 venv、测试、打包
├─ scripts/install_desktop_shortcut.ps1   # 安装桌面快捷方式
└─ scripts/hardware_acceptance.ps1        # 实机验收入口
```

---

### Task 1: 可重复的 Python 项目与固定配置

**Files:**
- Modify: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/qingzi_learning/__init__.py`
- Create: `src/qingzi_learning/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig`, `load_config() -> AppConfig`
- Consumes: Windows `%LOCALAPPDATA%`; no earlier project interfaces

- [ ] **Step 1: 写配置失败测试**

```python
from pathlib import Path
from qingzi_learning.config import load_config

def test_default_config_uses_fixed_knowledge_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = load_config()
    assert cfg.knowledge_root == Path(r"C:\晴子知识库\5th grade")
    assert cfg.subjects == ("语文", "数学", "英语")
    assert (cfg.camera_vid, cfg.camera_pid) == (0xBC15, 0x2C1B)
    assert cfg.spool_root == tmp_path / "QingziLearningAssistant" / "spool"
```

- [ ] **Step 2: 建立包配置并验证测试先失败**

`pyproject.toml` 使用 `src` 布局，运行依赖固定为 `opencv-python==4.13.0.92`、`cv2-enumerate-cameras==1.3.3`、`Pillow>=11,<13`、`jsonschema>=4.23,<5`，开发依赖包含 `pytest>=8.3,<10`。`.gitignore` 增加 `.venv/`、`build/`、`dist/`、`*.spec`、`__pycache__/`、`.pytest_cache/`。运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v
```

Expected: FAIL，原因是 `qingzi_learning.config` 尚不存在。

- [ ] **Step 3: 实现不可变配置模型**

```python
from dataclasses import dataclass
from pathlib import Path
import os

@dataclass(frozen=True)
class AppConfig:
    knowledge_root: Path
    subjects: tuple[str, ...]
    camera_vid: int
    camera_pid: int
    spool_root: Path
    app_data_root: Path
    subject_confidence_threshold: float = 0.85

def load_config() -> AppConfig:
    local = Path(os.environ["LOCALAPPDATA"]) / "QingziLearningAssistant"
    return AppConfig(
        knowledge_root=Path(r"C:\晴子知识库\5th grade"),
        subjects=("语文", "数学", "英语"),
        camera_vid=0xBC15,
        camera_pid=0x2C1B,
        spool_root=local / "spool",
        app_data_root=local,
    )
```

- [ ] **Step 4: 运行配置测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: PASS。

- [ ] **Step 5: 提交项目骨架**

```powershell
git add .gitignore pyproject.toml src/qingzi_learning tests/test_config.py
git commit -m "build: scaffold qingzi learning assistant"
```

---

### Task 2: 分析领域模型与严格 JSON Schema

**Files:**
- Create: `src/qingzi_learning/domain.py`
- Create: `src/qingzi_learning/schema/analysis-result.schema.json`
- Create: `tests/test_analysis_schema.py`

**Interfaces:**
- Produces: `Subject`, `GradingMode`, `QuestionStatus`, `CapturedPage`, `CapturedDocument`, `QuestionAnalysis`, `AnalysisResult`, `validate_analysis_payload(payload) -> None`
- Consumes: `jsonschema`; no storage or UI dependency

- [ ] **Step 1: 写三种批改模式和待确认规则测试**

```python
import json
from importlib.resources import files
from jsonschema import validate

def test_mixed_analysis_payload_matches_schema():
    schema = json.loads((files("qingzi_learning.schema") / "analysis-result.schema.json").read_text("utf-8"))
    payload = {
        "document_id": "doc-20260915-001",
        "subject": "数学",
        "subject_confidence": 0.96,
        "document_type": "试卷",
        "grading_mode": "mixed",
        "teacher_mark_evidence": ["page_002: red cross near question 4"],
        "questions": [{
            "question_id": "4", "page": 2, "prompt_summary": "分数除法应用题",
            "student_answer": "12", "reference_answer": "18", "status": "incorrect",
            "decision_source": "teacher", "knowledge_points": ["分数除法应用题"],
            "error_categories": ["列式"], "confidence": 0.93, "reason": "单位1识别错误"
        }],
        "summary": "本次主要问题是单位1识别。"
    }
    validate(payload, schema)
```

- [ ] **Step 2: 运行测试确认 Schema 缺失**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py -v`

Expected: FAIL，资源文件不存在。

- [ ] **Step 3: 定义枚举、不可变数据类和 Schema**

Schema 必须设置 `additionalProperties: false`，并把以下值写成枚举：

```json
{
  "subject": ["语文", "数学", "英语"],
  "grading_mode": ["teacher_marked", "auto_grade", "mixed"],
  "status": ["correct", "incorrect", "partial", "needs_review"],
  "decision_source": ["teacher", "model", "mixed"]
}
```

`QuestionAnalysis.counts_toward_mastery` 仅当 `status != needs_review` 且 `confidence >= 0.80` 时返回 `True`。

- [ ] **Step 4: 增加拒绝虚构结论测试并运行全文件**

```python
import pytest
from jsonschema import ValidationError

def test_schema_rejects_unknown_subject(valid_payload, schema):
    valid_payload["subject"] = "科学"
    with pytest.raises(ValidationError):
        validate(valid_payload, schema)
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py -v`

Expected: PASS。

- [ ] **Step 5: 提交领域契约**

```powershell
git add src/qingzi_learning/domain.py src/qingzi_learning/schema tests/test_analysis_schema.py
git commit -m "feat: define strict homework analysis contract"
```

---

### Task 3: 安全路径、SQLite 事务和幂等事实层

**Files:**
- Create: `src/qingzi_learning/storage/__init__.py`
- Create: `src/qingzi_learning/storage/paths.py`
- Create: `src/qingzi_learning/storage/schema.sql`
- Create: `src/qingzi_learning/storage/repository.py`
- Create: `tests/storage/test_repository.py`
- Create: `tests/storage/test_paths.py`

**Interfaces:**
- Produces: `KnowledgePaths.ensure_subject_tree(subject)`, `KnowledgeRepository.create_document(...)`, `save_analysis(...)`, `get_document(...)`, `list_pending()`, `dashboard_snapshot()`
- Consumes: `AppConfig`, Task 2 domain models

- [ ] **Step 1: 写路径越界和科目白名单测试**

```python
import pytest

def test_subject_tree_rejects_path_traversal(paths):
    with pytest.raises(ValueError, match="非法科目"):
        paths.ensure_subject_tree(r"..\其他")

def test_subject_tree_is_under_existing_subject_folder(paths):
    tree = paths.ensure_subject_tree("数学")
    assert tree.raw.parent.name == "数学"
    assert tree.raw.name == "原始资料"
```

- [ ] **Step 2: 写幂等分析测试**

```python
def test_saving_same_analysis_twice_does_not_duplicate_questions(repo, analysis):
    repo.save_analysis(analysis)
    repo.save_analysis(analysis)
    assert repo.count_questions(analysis.document_id) == len(analysis.questions)
```

- [ ] **Step 3: 运行测试确认存储模块缺失**

Run: `.\.venv\Scripts\python.exe -m pytest tests/storage -v`

Expected: FAIL。

- [ ] **Step 4: 实现目录守卫和 SQLite Schema**

`schema.sql` 创建 `documents`、`pages`、`questions`、`question_knowledge_points`、`knowledge_stats`、`processing_jobs` 六张表；`documents.document_id`、`questions(document_id, question_id)` 和 `processing_jobs.document_id` 必须唯一。所有写操作使用：

```python
with self.connection:
    self.connection.execute(...)
    self.connection.executemany(...)
```

路径通过 `Path.resolve()` 验证仍位于固定的科目根目录内，文件名只允许中文、英文字母、数字、连字符和下划线。

- [ ] **Step 5: 实现 upsert 并运行存储测试**

`save_analysis` 使用 `INSERT ... ON CONFLICT DO UPDATE`，更新前先删除同一文档旧的题目关联，再在同一事务内重建，保证重复分析不会重复计数。

Run: `.\.venv\Scripts\python.exe -m pytest tests/storage -v`

Expected: PASS。

- [ ] **Step 6: 提交事实层**

```powershell
git add src/qingzi_learning/storage tests/storage
git commit -m "feat: add recoverable idempotent knowledge storage"
```

---

### Task 4: 按 VID/PID 锁定证件拍照机

**Files:**
- Create: `src/qingzi_learning/camera/__init__.py`
- Create: `src/qingzi_learning/camera/devices.py`
- Create: `tests/camera/test_devices.py`

**Interfaces:**
- Produces: `CameraDescriptor`, `CameraNotFound`, `CameraBusy`, `find_document_camera(enumerator, vid, pid)`, `open_document_camera(descriptor)`
- Consumes: `cv2_enumerate_cameras.enumerate_cameras`, `cv2.VideoCapture`

- [ ] **Step 1: 写不能回退到 Logitech 的测试**

```python
import pytest
from qingzi_learning.camera.devices import CameraDescriptor, CameraNotFound, find_document_camera

def test_does_not_fallback_to_logitech_when_target_missing():
    devices = [CameraDescriptor(index=0, backend=700, name="Logi C270", vid=0x046D, pid=0x0825)]
    with pytest.raises(CameraNotFound):
        find_document_camera(lambda: devices, 0xBC15, 0x2C1B)
```

- [ ] **Step 2: 写目标设备选择测试**

```python
def test_selects_target_by_vid_pid_even_when_second():
    devices = [
        CameraDescriptor(0, 700, "Logi C270", 0x046D, 0x0825),
        CameraDescriptor(1, 700, "USB Camera", 0xBC15, 0x2C1B),
    ]
    found = find_document_camera(lambda: devices, 0xBC15, 0x2C1B)
    assert found.index == 1
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/camera/test_devices.py -v`

Expected: FAIL。

- [ ] **Step 4: 实现枚举与打开**

优先使用 `cv2.CAP_MSMF` 枚举；只有同一 VID/PID 的 MSMF 打开失败时才尝试该设备的 DSHOW 描述符，不得尝试其他摄像头。打开后连续读取 10 帧预热，任何读取失败抛出 `CameraBusy`。

- [ ] **Step 5: 运行单测并执行只读枚举冒烟测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/camera/test_devices.py -v
.\.venv\Scripts\python.exe -m cv2_enumerate_cameras
```

Expected: 单测 PASS；枚举输出包含 `BC15:2C1B` 和 `046D:0825`，程序选择前者。

- [ ] **Step 6: 提交设备选择模块**

```powershell
git add src/qingzi_learning/camera tests/camera
git commit -m "feat: lock capture to document camera hardware id"
```

---

### Task 5: 图片质量检查与可恢复连续拍摄会话

**Files:**
- Create: `src/qingzi_learning/camera/quality.py`
- Create: `src/qingzi_learning/capture/__init__.py`
- Create: `src/qingzi_learning/capture/session.py`
- Create: `tests/camera/test_quality.py`
- Create: `tests/capture/test_session.py`

**Interfaces:**
- Produces: `ImageQualityResult`, `check_image_quality(frame)`, `CaptureSession.capture(frame)`, `retake()`, `next_page()`, `finish()`, `recover(session_dir)`
- Consumes: `AppConfig.spool_root`, Pillow/OpenCV arrays, `CapturedPage`

- [ ] **Step 1: 写落盘后才能返回成功的测试**

```python
def test_capture_writes_page_before_reporting_success(session, clear_frame):
    page = session.capture(clear_frame)
    assert page.path.exists()
    assert page.path.name == "page_001.jpg"
    assert page.sha256 == sha256(page.path.read_bytes()).hexdigest()
```

- [ ] **Step 2: 写重拍不静默覆盖的测试**

```python
def test_retake_keeps_audit_copy(session, clear_frame, second_frame):
    first = session.capture(clear_frame)
    replacement = session.retake(second_frame)
    assert replacement.path.name == "page_001.jpg"
    assert (session.session_dir / "discarded" / first.sha256[:12] / "page_001.jpg").exists()
```

- [ ] **Step 3: 写模糊和过暗测试并确认失败**

```python
def test_blurry_dark_image_is_rejected(blurry_dark_frame):
    result = check_image_quality(blurry_dark_frame)
    assert not result.acceptable
    assert {"too_dark", "too_blurry"}.issubset(result.reasons)
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/camera/test_quality.py tests/capture/test_session.py -v`

Expected: FAIL。

- [ ] **Step 4: 实现质量阈值和原子写入**

质量检查至少包含最短边 `>= 1200`、灰度均值范围 `45..235`、Laplacian 方差 `>= 80`。图片写入先保存为 `.part`，`fsync` 后用 `Path.replace()` 原子替换为 JPG；每次状态变化写 `session.json`。

- [ ] **Step 5: 实现十页会话与恢复测试**

```python
def test_ten_page_session_recovers_in_order(make_session, ten_frames):
    session = make_session()
    for frame in ten_frames:
        session.capture(frame)
        session.next_page()
    restored = CaptureSession.recover(session.session_dir)
    assert [p.page_number for p in restored.pages] == list(range(1, 11))
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/camera/test_quality.py tests/capture/test_session.py -v`

Expected: PASS。

- [ ] **Step 6: 提交连续拍摄模块**

```powershell
git add src/qingzi_learning/camera/quality.py src/qingzi_learning/capture tests/camera/test_quality.py tests/capture
git commit -m "feat: add durable multi-page capture sessions"
```

---

### Task 6: 只读 Codex 多页分析适配器

**Files:**
- Create: `src/qingzi_learning/analysis/__init__.py`
- Create: `src/qingzi_learning/analysis/codex_cli.py`
- Create: `src/qingzi_learning/analysis/service.py`
- Create: `tests/analysis/test_codex_cli.py`
- Create: `tests/analysis/test_service.py`

**Interfaces:**
- Produces: `CodexCliAnalyzer.analyze(document) -> AnalysisResult`, `AnalysisService.analyze_or_queue(document)`
- Consumes: Task 2 Schema/models, captured page paths, injected `ProcessRunner`

- [ ] **Step 1: 写命令安全性测试**

```python
def test_codex_command_is_read_only_and_uses_argument_list(analyzer, document):
    analyzer.analyze(document)
    args = analyzer.runner.last_args
    assert args[0].lower().endswith("codex.cmd")
    assert args[1:3] == ["exec", "--skip-git-repo-check"]
    assert ["--sandbox", "read-only"] == args[args.index("--sandbox"):args.index("--sandbox") + 2]
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert args.count("--image") == len(document.pages)
```

- [ ] **Step 2: 写无登录、超时和非法 JSON 的排队测试**

```python
@pytest.mark.parametrize("failure", ["not_logged_in", "timeout", "invalid_json"])
def test_analysis_failure_queues_document_without_losing_pages(service, document, failure):
    service.analyzer.failure = failure
    result = service.analyze_or_queue(document)
    assert result.state == "pending"
    assert all(page.path.exists() for page in document.pages)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/analysis -v`

Expected: FAIL。

- [ ] **Step 4: 实现无 shell 的 Codex 调用**

用 `shutil.which("codex.cmd")` 查找 CLI，用 `subprocess.run(args, input=prompt, text=True, capture_output=True, timeout=600, shell=False)` 调用。参数必须包含：

```python
[
    codex_cmd, "exec", "--skip-git-repo-check",
    "--sandbox", "read-only",
    "--output-schema", str(schema_path),
    "--output-last-message", str(response_path),
    "-C", str(document.session_dir),
    *sum((["--image", str(page.path)] for page in document.pages), []),
    "-",
]
```

Prompt 明确要求逐页分析、老师批改优先、不可辨认时使用 `needs_review`、不得猜测姓名，返回内容必须符合 Schema。

- [ ] **Step 5: 实现响应校验和主观题保护**

`AnalysisService` 校验 JSON 后，把作文、开放式阅读题中 `decision_source == model` 且 `confidence < 0.90` 的结论强制转换为 `needs_review`。科目置信度低于 `0.85` 时返回 `needs_subject_confirmation`。

- [ ] **Step 6: 运行分析测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/analysis -v`

Expected: PASS；测试使用假 runner，不消耗 ChatGPT/Codex 额度。

- [ ] **Step 7: 提交分析适配器**

```powershell
git add src/qingzi_learning/analysis tests/analysis
git commit -m "feat: add read-only structured Codex analysis"
```

---

### Task 7: 科目归档、错题与长期掌握度更新

**Files:**
- Create: `src/qingzi_learning/knowledge/__init__.py`
- Create: `src/qingzi_learning/knowledge/updater.py`
- Create: `tests/knowledge/test_updater.py`

**Interfaces:**
- Produces: `KnowledgeUpdater.apply(analysis) -> UpdateSummary`
- Consumes: `KnowledgeRepository`, `AnalysisResult`, validated subject or user subject override

- [ ] **Step 1: 写待确认不计入掌握度测试**

```python
def test_needs_review_is_linked_but_not_counted(updater, analysis_with_review):
    updater.apply(analysis_with_review)
    stats = updater.repo.get_knowledge_stats("数学", "分数除法")
    assert stats.exposure_count == 0
    assert updater.repo.count_review_items() == 1
```

- [ ] **Step 2: 写老师判定与系统判定分开统计测试**

```python
def test_teacher_and_model_decisions_have_separate_counters(updater, mixed_analysis):
    updater.apply(mixed_analysis)
    stats = updater.repo.get_knowledge_stats("英语", "一般现在时")
    assert stats.teacher_incorrect_count == 1
    assert stats.model_incorrect_count == 1
```

- [ ] **Step 3: 写重复更新幂等测试并确认失败**

```python
def test_reapplying_document_does_not_double_mastery_counts(updater, mixed_analysis):
    updater.apply(mixed_analysis)
    first = updater.repo.get_knowledge_stats("英语", "一般现在时")
    updater.apply(mixed_analysis)
    second = updater.repo.get_knowledge_stats("英语", "一般现在时")
    assert second == first
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/knowledge -v`

Expected: FAIL。

- [ ] **Step 4: 实现事务内重算策略**

对某个 `document_id` 更新时，在同一事务中替换它的题目记录，然后从所有已确认题目重新聚合受影响的知识点，避免增量加减产生漂移。复习优先级使用确定性规则：近期错误、重复错误、未复测提高优先级；连续正确复测降低优先级。

- [ ] **Step 5: 运行知识库更新测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/knowledge -v`

Expected: PASS。

- [ ] **Step 6: 提交长期知识更新器**

```powershell
git add src/qingzi_learning/knowledge tests/knowledge
git commit -m "feat: update traceable mastery and error records"
```

---

### Task 8: Markdown 阅读层和知识库总览 HTML

**Files:**
- Create: `src/qingzi_learning/export/__init__.py`
- Create: `src/qingzi_learning/export/markdown.py`
- Create: `src/qingzi_learning/export/dashboard.py`
- Create: `tests/export/test_markdown.py`
- Create: `tests/export/test_dashboard.py`

**Interfaces:**
- Produces: `MarkdownExporter.export_document(document_id)`, `export_subject(subject)`, `DashboardExporter.export() -> Path`
- Consumes: `KnowledgeRepository.dashboard_snapshot()`, subject directories

- [ ] **Step 1: 写 Markdown 可追溯性测试**

```python
def test_analysis_markdown_links_question_to_original_page(exporter, stored_analysis):
    path = exporter.export_document(stored_analysis.document_id)
    text = path.read_text("utf-8")
    assert "source_page: 2" in text
    assert "page_002.jpg" in text
    assert "status: incorrect" in text
```

- [ ] **Step 2: 写自包含首页和后续功能标签测试**

```python
def test_dashboard_is_self_contained_and_marks_future_actions(dashboard):
    path = dashboard.export()
    html = path.read_text("utf-8")
    assert path.name == "知识库首页.html"
    assert "晴子学习知识库" in html
    assert "错题本" in html and "知识点地图" in html
    assert "生成考前复习包（后续功能）" in html
    assert "发起掌握度检验（后续功能）" in html
    assert "http://" not in html and "https://" not in html
```

- [ ] **Step 3: 运行导出测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/export -v`

Expected: FAIL。

- [ ] **Step 4: 实现原子生成**

Markdown 使用 YAML front matter 和相对链接。HTML 将快照数据 JSON 转义后内嵌，不加载 CDN、字体或远程脚本；生成到 `.part` 后原子替换 `C:\晴子知识库\5th grade\知识库首页.html`。

- [ ] **Step 5: 实现已确认的总览布局**

布局包含左侧导航、四项摘要、三科概况、薄弱知识点、最近更新、错题入口和待确认入口。所有指标旁显示统计周期和样本量；没有数据时显示“尚无数据”，不得伪造百分比。

- [ ] **Step 6: 运行导出测试并检查 HTML**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/export -v
Start-Process 'C:\晴子知识库\5th grade\知识库首页.html'
```

Expected: 测试 PASS；浏览器离线显示，无外部资源错误，所有原题链接留在知识库根目录内。

- [ ] **Step 7: 提交阅读层**

```powershell
git add src/qingzi_learning/export tests/export
git commit -m "feat: export Obsidian notes and local overview"
```

---

### Task 9: 业务编排和失败恢复

**Files:**
- Create: `src/qingzi_learning/workflow/__init__.py`
- Create: `src/qingzi_learning/workflow/controller.py`
- Create: `tests/workflow/test_controller.py`

**Interfaces:**
- Produces: `WorkflowController.finish_and_analyze(session)`, `confirm_subject(job_id, subject)`, `retry_pending(job_id)`, `recover_jobs()`
- Consumes: capture session, analyzer, repository, updater, exporters

- [ ] **Step 1: 写正常端到端编排测试**

```python
def test_finish_archives_then_updates_all_read_models(controller, session, fake_math_analysis):
    controller.analyzer.result = fake_math_analysis
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "completed"
    assert outcome.subject == "数学"
    assert outcome.analysis_markdown.exists()
    assert outcome.dashboard_path.exists()
    assert all(p.exists() for p in outcome.archived_pages)
```

- [ ] **Step 2: 写科目不确定时暂停测试**

```python
def test_low_subject_confidence_waits_for_user_choice(controller, session, low_confidence_analysis):
    controller.analyzer.result = low_confidence_analysis
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation"
    assert controller.repo.get_job(outcome.job_id).knowledge_applied is False
```

- [ ] **Step 3: 写中断恢复测试并确认失败**

```python
def test_restart_recovers_job_after_images_saved_before_analysis(controller_factory, session):
    first = controller_factory(crash_after="spool_saved")
    with pytest.raises(SimulatedCrash):
        first.finish_and_analyze(session)
    second = controller_factory()
    jobs = second.recover_jobs()
    assert len(jobs) == 1 and jobs[0].state == "pending"
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/workflow -v`

Expected: FAIL。

- [ ] **Step 4: 实现显式状态机**

状态只允许：`capturing -> spooled -> analyzing -> needs_subject_confirmation | needs_review | completed | pending`。每次跨状态先提交 SQLite，再进行下一项副作用；重启后根据最后提交状态继续。

当分析引擎完全失败、因而无法判断科目时，流程先返回 `needs_subject_confirmation`。用户选择科目后，原图归档到该科目的 `待处理` 并保留 `pending` 分析任务；不得把未知科目的图片放入任意默认科目。

- [ ] **Step 5: 实现归档次序**

科目确定后把暂存目录复制到目标科目的 `原始资料\YYYY\MM\document_id`，逐文件校验 SHA-256 后才删除暂存副本。随后依次保存分析、更新知识点、导出 Markdown、导出首页；任何后续失败均可从数据库重跑。

- [ ] **Step 6: 运行业务编排测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/workflow -v`

Expected: PASS。

- [ ] **Step 7: 提交编排模块**

```powershell
git add src/qingzi_learning/workflow tests/workflow
git commit -m "feat: orchestrate recoverable capture analysis workflow"
```

---

### Task 10: 单窗口 Tkinter 操作界面

**Files:**
- Create: `src/qingzi_learning/ui/__init__.py`
- Create: `src/qingzi_learning/ui/app.py`
- Create: `src/qingzi_learning/main.py`
- Create: `tests/ui/test_view_model.py`

**Interfaces:**
- Produces: `LearningAssistantApp`, `CaptureViewModel`, `main()`
- Consumes: `WorkflowController`, target camera, capture session

- [ ] **Step 1: 写按钮状态测试**

```python
def test_button_states_follow_capture_flow(view_model):
    assert view_model.can_capture and not view_model.can_finish
    view_model.on_page_captured()
    assert view_model.can_retake and view_model.can_next and view_model.can_finish
    view_model.on_next_page()
    assert view_model.page_number == 2 and view_model.can_capture
```

- [ ] **Step 2: 写关闭窗口不丢会话测试**

```python
def test_close_during_capture_persists_recovery_state(view_model, session):
    view_model.on_page_captured()
    view_model.on_close()
    assert (session.session_dir / "session.json").exists()
```

- [ ] **Step 3: 运行 UI 模型测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ui -v`

Expected: FAIL。

- [ ] **Step 4: 实现线程边界**

摄像头读取和 Codex 分析运行在后台线程；只有 Tk 主线程更新控件。后台结果通过 `queue.Queue` 送回，主线程用 `after(50, poll_events)` 轮询。窗口提供“拍摄本页、重拍、下一页、完成并分析、打开资料文件夹、打开知识库总览”。

- [ ] **Step 5: 实现明确的人机提示**

找不到目标摄像头时显示其硬件 ID，不切换摄像头；科目不确定时弹出三个大按钮；模糊/过暗提示原因并保留重拍入口；完成页显示保存目录、错题数、薄弱知识点和待确认数。

- [ ] **Step 6: 运行 UI 单测和本地启动**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui -v
.\.venv\Scripts\python.exe -m qingzi_learning.main
```

Expected: 单测 PASS；窗口启动、主按钮中文完整显示、关闭后进程退出且摄像头释放。

- [ ] **Step 7: 提交桌面 UI**

```powershell
git add src/qingzi_learning/ui src/qingzi_learning/main.py tests/ui
git commit -m "feat: add one-window capture and analysis UI"
```

---

### Task 11: 待确认题目的家长复核闭环

**Files:**
- Create: `src/qingzi_learning/review/__init__.py`
- Create: `src/qingzi_learning/review/service.py`
- Create: `src/qingzi_learning/ui/review_dialog.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `src/qingzi_learning/storage/schema.sql`
- Modify: `src/qingzi_learning/storage/repository.py`
- Modify: `src/qingzi_learning/knowledge/updater.py`
- Modify: `src/qingzi_learning/export/markdown.py`
- Modify: `src/qingzi_learning/export/dashboard.py`
- Create: `tests/review/test_review_service.py`

**Interfaces:**
- Produces: `ReviewService.list_pending()`, `confirm_question(document_id, question_id, final_status, corrected_answer, note) -> UpdateSummary`
- Consumes: `KnowledgeRepository`, `KnowledgeUpdater`, `MarkdownExporter`, `DashboardExporter`

- [ ] **Step 1: 写确认前后掌握度变化测试**

```python
def test_confirmed_review_item_only_counts_after_parent_decision(review_service, pending_question):
    before = review_service.repo.get_knowledge_stats("语文", "概括主要内容")
    assert before.exposure_count == 0
    review_service.confirm_question(
        pending_question.document_id,
        pending_question.question_id,
        final_status="incorrect",
        corrected_answer="人物、事件、结果均需概括",
        note="家长对照老师答案确认",
    )
    after = review_service.repo.get_knowledge_stats("语文", "概括主要内容")
    assert after.exposure_count == 1 and after.incorrect_count == 1
```

- [ ] **Step 2: 写无效确认拒绝测试**

```python
def test_review_rejects_needs_review_as_final_status(review_service, pending_question):
    with pytest.raises(ValueError, match="最终状态"):
        review_service.confirm_question(
            pending_question.document_id,
            pending_question.question_id,
            final_status="needs_review",
            corrected_answer="",
            note="",
        )
```

- [ ] **Step 3: 运行测试确认复核服务缺失**

Run: `.\.venv\Scripts\python.exe -m pytest tests/review -v`

Expected: FAIL。

- [ ] **Step 4: 实现复核事务和重新导出**

`confirm_question` 在事务中保存家长最终状态、修正答案、备注和确认时间；随后重算受影响知识点，并重新生成该文档 Markdown、科目索引和知识库首页。审计记录保留原模型状态与置信度，不覆盖模型原始输出。

- [ ] **Step 5: 实现复核对话框**

桌面程序提供“待家长确认”入口。对话框显示原图页、题号、识别到的答案、系统理由和置信度；用户必须选择“正确、错误、部分正确”之一才能保存，可补充正确答案和备注。保存后自动跳到下一项。

- [ ] **Step 6: 运行复核测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/review -v`

Expected: PASS。

- [ ] **Step 7: 提交人工复核闭环**

```powershell
git add src/qingzi_learning/review src/qingzi_learning/ui src/qingzi_learning/storage src/qingzi_learning/knowledge src/qingzi_learning/export tests/review
git commit -m "feat: add parent review for uncertain grading"
```

---

### Task 12: 打包、桌面快捷方式与无密钥检查

**Files:**
- Create: `requirements-build.txt`
- Create: `scripts/build.ps1`
- Create: `scripts/install_desktop_shortcut.ps1`
- Create: `tests/test_packaging_assets.py`

**Interfaces:**
- Produces: `dist/晴子学习助手/晴子学习助手.exe`, desktop shortcut `晴子学习助手.lnk`
- Consumes: all application modules; PyInstaller `6.22.3`

- [ ] **Step 1: 写打包资产测试**

```python
def test_packaging_scripts_never_contain_api_keys(project_root):
    paths = [project_root / "scripts" / "build.ps1", project_root / "scripts" / "install_desktop_shortcut.ps1"]
    assert all(path.exists() for path in paths)
    text = "\n".join(path.read_text("utf-8") for path in paths)
    assert "OPENAI_API_KEY=" not in text
    assert "sk-" not in text
    assert "--dangerously-bypass-approvals-and-sandbox" not in text
```

- [ ] **Step 2: 运行测试确认脚本尚不存在**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_packaging_assets.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现构建脚本**

`build.ps1` 必须依次执行全量测试、安装 `pyinstaller==6.22.3`、运行 PyInstaller，并把 JSON Schema 收入包：

```powershell
$ErrorActionPreference = 'Stop'
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed `
  --name '晴子学习助手' `
  --collect-all cv2_enumerate_cameras `
  --add-data 'src\qingzi_learning\schema\analysis-result.schema.json;qingzi_learning\schema' `
  src\qingzi_learning\main.py
```

- [ ] **Step 4: 实现快捷方式脚本**

脚本用 `[Environment]::GetFolderPath('Desktop')` 定位桌面，用 `WScript.Shell.CreateShortcut()` 创建 `.lnk`；目标必须是构建产物的绝对路径，工作目录为程序目录，描述为“连续拍摄作业并更新晴子知识库”。如果目标 EXE 不存在则立即失败且不创建快捷方式。

- [ ] **Step 5: 运行测试、构建并检查产物**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_packaging_assets.py -v
.\scripts\build.ps1
Get-Item -LiteralPath '.\dist\晴子学习助手\晴子学习助手.exe'
```

Expected: PASS；EXE 存在且可启动。

- [ ] **Step 6: 安装并验证快捷方式**

```powershell
.\scripts\install_desktop_shortcut.ps1
$desktop = [Environment]::GetFolderPath('Desktop')
Get-Item -LiteralPath (Join-Path $desktop '晴子学习助手.lnk')
```

Expected: 快捷方式存在，双击打开程序。

- [ ] **Step 7: 提交部署脚本**

```powershell
git add requirements-build.txt scripts tests/test_packaging_assets.py
git commit -m "build: package and install desktop learning assistant"
```

---

### Task 13: 三科样例与实际拍照机端到端验收

**Files:**
- Create: `tests/fixtures/analysis/teacher_marked_chinese.json`
- Create: `tests/fixtures/analysis/unmarked_math.json`
- Create: `tests/fixtures/analysis/mixed_english.json`
- Create: `tests/e2e/test_three_subject_workflow.py`
- Create: `scripts/hardware_acceptance.ps1`
- Create: `docs/verification/v1-acceptance.md`

**Interfaces:**
- Produces: 可审计的自动化与实机验收记录
- Consumes: complete application, target camera, current Codex login, three user-provided sample documents

- [ ] **Step 1: 写三科契约端到端测试**

```python
@pytest.mark.parametrize("fixture_name,subject", [
    ("teacher_marked_chinese.json", "语文"),
    ("unmarked_math.json", "数学"),
    ("mixed_english.json", "英语"),
])
def test_subject_fixture_updates_correct_tree(app_harness, fixture_name, subject):
    outcome = app_harness.run_fixture(fixture_name)
    assert outcome.subject == subject
    assert outcome.dashboard_path.exists()
    assert subject in outcome.analysis_markdown.parts
```

- [ ] **Step 2: 运行除硬件外的全量测试**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 全部 PASS，无真实 Codex 调用。

- [ ] **Step 3: 创建硬件验收脚本**

`hardware_acceptance.ps1` 依次检查：目标 PnP 设备状态、Codex 登录状态、知识库三个目录可写、程序启动、十页连续拍摄、重拍一页、完成归档。脚本只汇总检查结果，不自动伪造用户在 UI 中的拍摄动作。

- [ ] **Step 4: 用证件拍照机完成十页捕获验收**

用户把无敏感信息的测试纸放在设备下，完成 10 页拍摄并重拍其中 1 页。验证：只有 `VID_BC15&PID_2C1B` 被打开；归档文件为 `page_001.jpg` 至 `page_010.jpg`；重拍旧图位于审计目录；每张图可正常打开。

- [ ] **Step 5: 用真实三科样例完成分析验收**

分别测试老师已批改的语文、未批改的数学和混合批改的英语资料。人工核对科目、页序、老师标记优先级、待确认保护、错题数、知识点以及原题链接。真实调用次数和结果写入验收文档，但不得记录账户凭据。

- [ ] **Step 6: 测试失败恢复与幂等**

在一份无敏感信息的测试任务中断开网络并点击分析，确认原图进入待处理；恢复网络后重试两次，确认只存在一份文档和一组统计。

- [ ] **Step 7: 写入验收证据并提交**

`docs/verification/v1-acceptance.md` 记录命令、时间、通过/失败、样例类型、生成路径和人工核对结果。运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git status --short
```

Expected: 全量测试 PASS；除验收产生的本机知识库资料外，项目工作树只包含计划提交的验收记录。

```powershell
git add tests/fixtures tests/e2e scripts/hardware_acceptance.ps1 docs/verification/v1-acceptance.md
git commit -m "test: verify first release on document camera"
```
