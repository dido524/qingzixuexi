# 晴子学习助手 v1 验收记录

日期：2026-09-16（Asia/Shanghai）。此记录区分软件验收、真实模型验收和物理拍摄验收。

## 当前结论

- 三科离线端到端链路以及真实 Codex 三科合成练习分析已通过。
- 最终 Windows 安装包已重新构建，已安装 EXE 的生产相机 helper 链路烟测也通过；安装/hash 结果见下方交付段。
- **十页实机拍摄及重拍验收尚未完成。** 用户放纸后，已安装 EXE 的生产 helper 成功拍到 1600×1200 数学资料。人工看图确认大标题可读，但小字/算式明显过曝，纸面左右及底部出框，不能可靠判卷；需单张纸完整居中入框、调低直射照明后继续。
- 用户授权后，仅在独立验收目录保存了 1 张真实摄像头预览，未上传。所有已上传分析的图片均为本次验收本地生成的合成练习；未向孩子的知识库写入样例成绩。

## 修复与根因证据

### Windows 原子替换

原 capture/session、analysis/service、export/safe_write、publication 使用多套无重试的替换写入。Windows 中一个未开放 DELETE sharing 的读取句柄可稳定使 `os.replace` 抛出 WinError 5；新增真实 `CreateFileW` 句柄用例复现该原因。不能把所有 Access Denied 当成临时错误。

统一采用共享写入原语：同目录独占唯一临时文件，flush/fsync 后关闭写句柄；仅对 Windows 32/33，或者经 DELETE-access 探测确认为可恢复的 5，做最长 1 秒指数退避。只读文件、ACL 拒绝、非法路径不盲目重试。每次替换前重查边界/重解析点，失败后清理临时文件，错误文本不含用户路径。publication 保持既有发布锁和持久化恢复清单。

覆盖：真实短时占用、注入 5/32/33、永久错误、只读文件、期限、重试间路径变化、4 个并发 writer × 30 次完整文件写入、已有崩溃恢复与并发发布测试。

### 相机启动与逐帧期限

原读帧的 1–2 秒期限包含 Windows spawn/import 时间。新增 `ready` / `failed` / `not_found` 握手，进程启动、设备打开、10 帧预热使用独立 10 秒期限，随后每次读帧使用独立期限。关闭请求可打断启动等待，helper 在 owner worker 中终止、join、关闭 IPC，旧响应不能进入后续拍摄。慢 spawn、慢 open、阻塞 open/preview/capture 与新 helper 的新帧测试均覆盖。

实机普通 UVC 初始分辨率是 800×600，低于 1200px 质量门限；现在预热前请求 1600×1200，捕获时仍以实际返回图像做质量检查。

### 已安装 CLI 版本不兼容

PATH 中 npm `codex-cli 0.137.0` 无法解析新版模型目录，并明确返回当前模型需要新版 Codex。桌面应用已安装 `0.154.0-alpha.6.2`。程序现在发现桌面安装目录中的 runtime，检查版本、只读分析所需 flags、当前 ChatGPT 登录，再选择可用 runtime；无合格桌面 runtime 时检查 PATH 回退。版本按语义版本的三个数字核心比较，低于本机实测支持下限 `0.154.0` 的候选会跳过，当前 `0.154.0-alpha.*` 可接受；格式不完整则拒绝，未来数字版本仍需通过功能/登录检查。未升级全局 npm、未修改账号、未保存密钥或更改模型默认值。

## 自动化命令与结果

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests\ui\test_camera_process.py tests\ui\test_view_model.py -q
.\.venv\Scripts\python.exe -m compileall -q src scripts\acceptance_probe.py scripts\verify_capture_evidence.py
```

- 相机与 UI 专项：37 passed in 26.27s。
- 完整通用测试最终结果（含相机安装包烟测入口及 CLI 版本下限测试）：**362 passed, 1 skipped in 312.38s**。唯一跳过项为本机无创建 symlink 权限，重解析点防护另有非特权测试。
- 自动化测试仅替换外部模型进程边界，不发起真实 Codex 分析，不使用真实摄像头，不排除 camera_process 模块。
- 三科 fixture 各有两页，真实执行 CaptureSession、Codex 适配器解析、本地保护、SQLite、知识统计、发布/Markdown/HTML、家长确认、反复重试。可控网络失败后重试两次仍只有一份文档和一组统计，未断开电脑网络。

## 真实三科模型验收

命令：

```powershell
.\.venv\Scripts\python.exe scripts\acceptance_probe.py synthetic --root C:\Users\Home\Documents\qingzi-acceptance-20260916 --live
.\.venv\Scripts\python.exe scripts\acceptance_probe.py verify-synthetic --root C:\Users\Home\Documents\qingzi-acceptance-20260916
```

结果目录：`C:\Users\Home\Documents\qingzi-acceptance-20260916`。`synthetic-results.json` 记录资料编号、原图哈希、模型结果与生成路径；`knowledge\知识库首页.html` 是隔离的演示总览。

| 资料 | 科目/模式 | 人工逐图核对结果 | 状态 |
| --- | --- | --- | --- |
| `acceptance-chinese-baf15d72` | 语文 / teacher_marked | 静→动、大→小，两题红勾，两题均以 teacher 为来源 | completed |
| `acceptance-math-2ec0c017` | 数学 / auto_grade | 3/4+1/4=1 正确；2/5+1/5=3/10 错误，参考3/5，知识点同分母分数加法 | completed |
| `acceptance-english-a9e46464` | 英语 / mixed | 老师红叉 have、红字 has 优先判错；无批改 am 自动判对 | completed |

三科各 2 题，共 3 文档 6 题。逐张查看了合成图并核对科目、老师痕迹、答案、错题及知识点；三个 Markdown 和总览均生成。再对已完成任务各重试两次：3 文档、6 题保持不变，零额外模型调用，发布清单与所有文件哈希一致。

真实模型尝试共 **8 次**：旧 CLI 4 次失败（包含一次诊断重试）；新 CLI 1 次返回正确 JSON，但过深的验收目录导致路径长 276 字符、本机 LongPathsEnabled=0，Python 未读到输出；移到上述短路径后 3 次完整通过。另一次在 Store Python 重定向 LocalAppData 时被路径护栏阻止，发生在模型调用前，不计入次数。未更改系统长路径或隐私策略。

## 真实硬件检查

- PnP：`USB Camera`，Class `Camera`，Status `OK`，ProblemCode `0`。
- 唯一目标：`USB\VID_BC15&PID_2C1B&MI_00\6&29C808CF&0&0000`。
- 枚举同时看到 Logitech，但所有打开操作只针对目标 VID/PID；未打开 Logitech。
- Store Python 3.13 开发环境的两个 backend 打开失败；标准 Python 3.12 加载同一 OpenCV/枚举库后，MSMF 可打开目标设备。Windows 摄像头隐私设置为 Allow；无本任务遗留相机 helper。
- 一次精确 PnP restart-device 尝试返回 Access is denied，未重启设备或全局服务。随后通过普通 runtime 成功取帧，故无需以硬件重启作为必需步骤。
- 仅在内存查看的 1600×1200 预览为黑画面。40 帧后灰度/颜色总体均值约 0.0014/255，最大值27。此状态不符合拍摄质量门限，未落盘，未调用模型。
- **已安装 EXE 真实相机烟测通过**：`--camera-smoke-check C:\Users\Home\Documents\qingzi-acceptance-20260916\installed-camera-smoke-313.json`，exit `0`。报告 `frozen=true`、`python_version=3.13.14`、`frame_received=true`、`helper_closed=true`，设备为 `BC15:2C1B` / `MSMF`，实际帧 `1600×1200`；退出后 app/helper 孤儿进程 `0`。此命令使用生产 `CameraProcess → configured_camera_factory → VID/PID 选择 → OpenCV`，只保存 JSON 元数据，不保存、显示、上传或分析帧。
- 已安装包自身已证明可以收到帧，继续采用当前 Python 3.13.14 / `python313.dll` 构建，无需切换到 3.12。Store Python 开发运行环境的失败不能推断成冻结安装包失败。烟测中的 `quality_acceptable=false`、`too_dark/too_blurry`、灰度均值 `0.001493` 仍说明当前现场黑暗，需要物理放纸后才能完成拍摄验收。
- `hardware_acceptance.ps1` 验证三个固定科目目录存在且可写（只创建独占临时探针后删除）、CLI 登录、已安装程序安全 smoke、桌面快捷方式；缺少十页/重拍证据时明确报告 null，绝不伪造 UI 操作。

物理十页验收完成后，可运行：

```powershell
scripts\hardware_acceptance.ps1 -SessionDirectory <会话目录> -ArchiveDirectory <归档目录>
```

它核对 page_001.jpg 到 page_010.jpg、可解码/哈希、较早页重拍审计副本与完成 sidecar。

## 远程期间的十页后续流程替代验收

用户暂时无法在现场重新摆放纸张，因此没有把合成输入冒充为物理实拍。为验证真实拍照之后的全部软件链路，在隔离目录 `C:\Users\Home\Documents\qingzi-acceptance-20260916\downstream` 使用 10 张本地生成、无敏感信息、1600×1200 的数学页替代相机帧，仍走正式 `CaptureSession → WorkflowController → CodexCliAnalyzer → Repository → KnowledgeUpdater → Markdown/Dashboard`。

- 连续保存恰好 `page_001.jpg` 至 `page_010.jpg`，全部可解码且为 1600×1200；恢复会话后 10 页元数据保持一致。
- 较早页 `page_003.jpg` 完成重拍。原图 SHA-256 为 `E45B51E2F1A1D0ADC6504CC4C81E61B553BD6E0CBFF6EDD10DB0C299A6BD2896`；重拍后为 `1C2413097226ECBE40EFDE58110761B18E7361DFB2849E3DBCC6314510A97A9E`；`discarded` 审计副本与原图哈希完全相同。
- 真实 Codex 仅调用 1 次，使用兼容桌面 CLI；结果为数学 / `auto_grade`，10 页对应 10 题，5 对 5 错。归档 10 页哈希与捕获证据完全一致，原图链接、Markdown、Dashboard 和发布清单均有效。
- 完成后再执行 Finish 2 次、Retry 2 次：模型调用仍为 1，documents=1、pages=10、questions=10、question_knowledge_points=20、knowledge_stats=3，完整事实指纹无变化。
- 独立恢复验收注入一次 `cli_failed`：首次不写入文档/题目/统计，原图与失败状态持久保存；确认数学后进入待处理；第一次 Retry 完成分析；再次 Retry 不增加调用或统计。分析器总调用恰好 2 次（失败 1、成功 1）。
- 证据文件：`capture-result.json`、`workflow-result.json`、`model-call.json`、`recovery-result.json`。这些文件和全部合成图片只存在于隔离验收目录，不写入孩子正式知识库。

结论：十页会话、较早页重拍审计、真实分析、归档、知识更新、发布、重复完成/重试幂等以及失败恢复均已通过。**仍待现场完成的只有清晰纸面的真实十页采集**；当前物理预览存在越界和过曝，不能记为通过。

## 最终安装与交付

### 用户放纸后的取图复核

新增显式诊断参数 `--camera-preview-output`，只能与 `--camera-smoke-check` 同用，并必须显式设置 `QINGZI_CAMERA_ACCEPTANCE_ROOT`。输出必须是该绝对根下的 `.jpg`，拒绝相对路径、越界、重解析点、正式知识库及其祖先、与报告重名；采用既有原子写入。默认烟测仍不保存图像，整个诊断不构造 UI、资料库或分析器。

本次进程临时环境变量为 `C:\Users\Home\Documents\qingzi-acceptance-20260916\physical`；保存的是用户已放好的测试纸。已安装 EXE exit 0，`frozen=true`、Python 3.13.14、BC15:2C1B / MSMF、1600×1200、helper_closed=true，退出后孤儿 app/helper 为 0。

- 预览：`C:\Users\Home\Documents\qingzi-acceptance-20260916\physical\preview.jpg`。
- SHA-256：`3A9937CA5AB9B815C563243588CC062DE91B606A31FE34E32442BB85690A5D3C`。
- 自动指标 `quality_acceptable=true`、灰度均值 234.804；人工逐图检查发现它不足以保证文字可读：桌面约占上方四分之一，书页部分越出视野，大片小字过曝。没有据此宣称十页验收通过，未进行真实纸面模型调用。
- 最小现场调整：用单张测试纸完整居中入框，调低或关闭直射纸面的补光/台灯，使小字和分数清晰。
- 本轮新增诊断测试先失败后通过；最终诊断专项 12 passed，相机/helper 联合专项 23 passed（最后增加一条重解析点测试前），打包专项 8 passed，compileall 通过。先前全量 362 passed、1 skipped 是诊断入口加入前的历史结果。
- 完成远程下游验收与本文更新后，当前最终 HEAD 重新执行完整通用测试：**372 passed, 1 skipped in 321.30s**；唯一跳过仍为本机无创建 symlink 权限，没有排除任何相机模块。

```powershell
scripts\build.ps1 -SkipTests
scripts\install_desktop_shortcut.ps1
scripts\hardware_acceptance.ps1
```

构建前后的完整通用测试单独执行，所以构建命令使用 SkipTests，避免再次重复同一全量测试。版本 `0.1.0`；安装位置 `%LOCALAPPDATA%\QingziLearningAssistant\app\晴子学习助手.exe`，桌面快捷方式 `晴子学习助手.lnk`。

当前 dist 与已安装 EXE 的 SHA-256 相同：`1AC75036573FC9158919DE59924DCA77E9D13B7E0A56D695E926FF6B1E8B94D0`。此包包含上述显式预览诊断入口；原默认行为不变。

重新构建及回滚安全安装均完成。`hardware_acceptance.ps1` 对已安装 EXE 的 `--smoke-check` 报告通过，版本 `0.1.0`，严格/传输 JSON Schema 和 SQLite schema.sql 均存在；快捷方式 target、working directory 和 icon 均正确。烟测结束后 `晴子学习助手` 进程数为 0。三科实际目录的临时可写性探针已删除，目录中未加入合成练习成绩。

## 2026-09-17：混合学科分页的软件验收（Task 9）

本节是新增的**合成自动化验收**，不改变上文 2026-09-16 的历史相机、真实模型及已安装程序结论。输入为测试中生成的随机合成图，模型响应由进程边界 fixture 提供；没有真实摄像头采集、真实模型分析，也没有向孩子正式知识库写入成绩。本任务只构建 dist 包，不更新桌面安装。

新增 `tests/fixtures/analysis/mixed_subject_analysis.json`：六页、每页一道题，第 1–4 页数学、第 5–6 页英语，逐页置信度均不低于 0.95，均无需确认。实际执行 `CaptureSession → CodexCliAnalyzer → WorkflowController → SQLite → Markdown/Dashboard/PublicationCoordinator`。

- 六页一次模型进程调用后自动完成，不进入整批选科；生成固定的 `six-page-mixed--math` 和 `six-page-mixed--english`，父任务没有 document 行。
- 数学保留原页号 1、2、3、4，英语保留 5、6；归档图像逐张可解码、SHA-256 与捕获图一致。两份子文档的题目、知识统计与 Markdown 分别核对完整预期知识点集合，避免只检查科目文字的弱断言。
- 再次 Finish 两次，父任务及两个子任务各 Retry 两次，完整事实和知识统计行保持一致，模型调用仍为一次，发布清单保持 current。
- 低置信度/需人工确认页面逐页确认，恢复后保留已确认页，最后一页确认直接完成拆分和发布，无需再次点击分析。
- 从关闭后重新打开的 SQLite 恢复旧 journal：缓存 analysis 不含 `page_subjects`，保留整批确认入口，不要求补齐逐页映射；确认、发布和重复重试期间模型调用为零。
- 既有语文、数学、英语三科端到端 fixture 保留，并补充单科不生成子文档、归档图哈希不变的断言。

### 本次测试和分发包结果

```powershell
.venv\Scripts\python.exe -m pytest tests/e2e/test_mixed_subject_workflow.py tests/e2e/test_three_subject_workflow.py -q
.venv\Scripts\python.exe -m pytest tests/test_analysis_schema.py tests/analysis/test_codex_cli.py tests/workflow/test_subject_split.py tests/workflow/test_controller.py tests/storage/test_repository.py tests/knowledge/test_updater.py tests/ui/test_view_model.py tests/ui/test_page_subject_dialog.py tests/ui/test_taskbar.py tests/e2e/test_mixed_subject_workflow.py -q
.venv\Scripts\python.exe -m pytest -q -rs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1 -SkipTests
& 'dist\晴子学习助手\晴子学习助手.exe' --smoke-check
```

- 端到端：**7 passed in 16.90s**；计划分层集合：**208 passed, 2 skipped in 143.75s**。
- 本任务只执行一次完整通用测试：**493 passed, 1 skipped in 477.14s (0:07:57)**，exit 0。唯一跳过为 `tests/export/test_safe_write.py:176` 的 `Windows symlink privilege is unavailable`，是上文已记录的权限限制；全量中的 Tk 窗口用例均通过，没有排除相机模块。
- 新增验收首轮即通过，说明前序任务已实现集成行为；本轮没有生产代码修改，也不声称存在先失败后修复的 RED 过程。
- 构建 exit 0；严格/传输 JSON Schema、SQLite `schema.sql` 均存在。直接读取 EXE 内嵌 Python archive 确认包含 `PIL.Image`、`PIL.ImageTk`、`tkinter`、`qingzi_learning.ui.page_subject_dialog`、`qingzi_learning.ui.taskbar`；包启动烟测完成这些入口的导入，**exit 0，4.293 秒**，未打开摄像头或调用分析。
- 本次 dist EXE SHA-256：`8764D96F523B05653DF90972FC00D351A25129ED42C9EE3D2970737AF543C35A`。此哈希仅指本次构建，不代表已安装桌面程序；安装由 Task 10 负责。
- 完整输出保留在 `.superpowers/sdd/2026-09-16-mixed-subject-page-splitting/task-9-{focused,full-pytest,build,smoke,package-contents}.log`，属于忽略的本地验收产物。

### 待现场执行的混合学科验收

以下是人工检查步骤，**尚未记为通过**；自动化不能证明纸面可读性、真实模型分类准确率或任务栏提醒的现场视觉效果。

1. 连续拍摄六页清晰资料（前四页数学、后两页英语），完成拍摄后观察无需整批选科；核对数学、英语归档分别四页和两页，仍使用原页号。
2. 使用一页难以判定或混合题材的资料触发逐页确认，检查大图预览及缩放，确认底部科目按钮始终可见；最后一页确认后应自动继续发布。
3. 切换到其他窗口后触发待确认，观察任务栏闪烁；返回应用完成确认，检查提醒停止。
4. 确认后重启应用并重试父任务/子任务，核对不重复分析、不重复计入知识统计。
5. 打开数学和英语知识库、分析 Markdown 与首页，检查题目、知识点及原图链接各归所属学科，原图完整可读。

## 2026-09-17：本机最终验收补充（Task 10）

原生 Windows Tk 探针发现任务栏调用拿到了 Tk 内部子窗口 HWND；它与 Win32 `GetAncestor(..., GA_ROOT)` 返回的顶层窗口不同。新增真实 Tk 回归先失败，再将应用通知句柄改为 `wm_frame()`，覆盖任务栏闪烁及前台窗口比较所需的同一顶层窗口。定向回归 **7 passed in 4.10s**。此验证证明句柄选择正确，不等同于肉眼观察到任务栏闪烁。

实际 `LearningAssistantApp → WorkflowWorker → WorkflowController → SQLite` 使用合成试卷图和本地 fixture 分析器进行自动化验收，正式孩子知识库未写入测试成绩。短路径根目录为 `C:\Users\Home\AppData\Local\Temp\qz-ui-qu3odb9k`：

- 单科成功：`single\knowledge` 中 `capture-7eb6e9d3409a4a4781c4adde91093571`，数学第 1、2 页。
- 混合成功：`mixed\knowledge` 中 `capture-3568d1d40bfd4bc2a297c4ce403fa127--math` 第 1–4 页，以及 `--english` 第 5–6 页。
- 待确认批次 `capture-09c1c2ce5b454778be3965ab9f95f841` 的第 2 页人工选择通过真实按钮回调保存，此时仍无部分文档。新进程恢复同一 SQLite 后只剩第 6 页，禁止分析器再次调用；选择英语后发布两个 4/2 页子文档。全部归档 SHA-256、三个场景的 `知识库首页.html` 存在性检查通过。
- 低置信度窗口在 870×595、最大化和恢复后的底部位置与按钮 `winfo_viewable` 断言通过；这是实际 Tk 的程序检查，未声称人工视觉验收。

本机桌面在检查时处于 Windows 锁屏。选定应用窗口后取得的是锁屏画面，停止桌面输入；未解锁、未操作认证界面。完整视觉检查、后台闪烁视觉效果、真实相机和真实模型准确率仍待现场完成。仅终止了本任务自己的合成验收进程；未终止用户的已安装程序。

修复后完整执行 `.venv\Scripts\python.exe -m pytest -q -rs`：**494 passed, 1 skipped in 1104.58s (0:18:24)**，exit 0。唯一跳过仍为 Windows symlink 权限用例；真实 Tk 用例全部通过。新包重新构建成功，`--smoke-check` **exit 0，11.072 秒**，SHA-256：`3132AC324A16261D007D25EEA8479F75A0C819BA51450CE010DF1985ED5F02D8`。此值是 dist 包，不是旧的桌面安装版本。

桌面安装暂未替换：现有程序仍运行，目标为 `C:\Users\Home\Documents\QingziLearningAssistant\app\晴子学习助手.exe`；图标为该安装根下 `assets\qingzi-photo.ico,0`。锁屏时无法确认旧实例是否有进行中的任务，因此未强行关闭或覆盖。后续安装需使用该实际 `-InstallRoot` 并保留自定义图标；不能直接采用安装脚本的默认目录和默认 EXE 图标。

## 2026-09-17：最终分支审查修复

本轮定向回归补齐了原验收未覆盖的并发与恢复情形：两条真实 SQLite 连接交错确认时，旧操作通过事务内快照比较退出，仍待确认的不同页面选择合并保存；不会覆盖较新的父任务完成状态或丢失子文档关联。分析失败并已归档到待处理目录的任务，可以在重试成功后拆分为多学科文档；只有未应用知识、无题目或复核证据、页面清单完全匹配的失败归档占位记录才在子文档事务中转换，异常时整个事务回滚。已删除 spool 图片的情况及中断的源图清理也覆盖在内。

实际 Tk 应用、worker 命令分发和控制器联合回归验证：最后一页选择保存后中断，恢复列表的“重试”直接继续发布，不再误开旧整批选科窗口；确认进行中关闭窗口，结果仍解除本操作的忙碌状态并刷新恢复任务，不改动后来打开的窗口；进度正确显示 `1/2 → 2/2`；启动自动打开首个待确认任务，其余任务排队，不替换已有确认或复核窗口。

生产提示词、传输及本地 Schema 已明确：单页含多个学科或内容不足时，无论置信度高低都要求 `needs_confirmation=true`。测试检查真实分析器传到进程边界的提示词和 Schema，并验证高置信度的确认标志仍使计划暂停；这不是对真实模型遵循率的证明。`ResolvedPageSubject` 仍仅由经过校验的内部计划构造，本轮未扩展其外部接口。

完整 RED/GREEN、联合回归、全量验证及分发包记录见本地 `.superpowers/sdd/2026-09-16-mixed-subject-page-splitting/final-fix-report.md`。本轮仍只重建 dist，不安装桌面程序、不终止现有用户应用；真实纸面、真实模型准确率与任务栏闪烁视觉效果仍保留为现场验收项。

本轮最终完整测试仅执行一次：`.venv\Scripts\python.exe -m pytest -q -rs`，**512 passed, 1 skipped in 477.71s (0:07:57)**，exit 0。唯一跳过为 `tests/export/test_safe_write.py:176` 的 Windows symlink 权限限制；最终全量中的全部 Tk 窗口测试均执行通过。此前联合回归为 **375 passed, 1 skipped in 408.78s**，当时跳过的一项 Tk 最大化布局测试已在本次最终全量中通过。

`scripts/build.ps1 -SkipTests` 构建 exit 0；新 dist 的 `--smoke-check` **exit 0，6.742 秒**，版本 `0.1.0`。当前分发 EXE SHA-256：`2C0544F686AFE9DFCB510885047FA32CDBA1846A216B8E61B06F40E4B68C9B4B`。构建与烟测的完整输出保存在同目录的 `final-fix-build.log`、`final-fix-smoke.log`；此哈希不代表旧桌面安装程序。

## 2026-09-19：数学课程知识图

家长提供的三张新教材照片核实了北师大版数学五年级上册目录。版本化目录只记录能从封面、扉页和目录确认的 12 个条目；知识关联均标为“教学整理建议”，现有错题和掌握度没有自动按名称归到单元。照片只归档在本机 `C:\晴子知识库\5th grade\数学\课程体系\bnu-math-g5-upper-2024-review\教材实拍`，每张复制后 SHA-256 与下载原图相同，未加入公开仓库。

目录顺序和页码、首页数学入口、Markdown 相对链接、兴趣班/下一年级目录独立导出已由测试核对。桌面程序已关闭、备份后安装新版；安装版烟测通过，资料库哈希和资料/题目数量未变。真实桌面点击与 Obsidian 导入视觉效果仍待家长现场体验，不把自动化结果冒充人工验收。

独立代码审查发现并已针对性修复：父母手改 Obsidian 笔记需要保留；构建脚本必须指定当前工作树源码；以后新增目录必须由首页和发布清单自动发现。第二轮审查发现家长笔记删除后可能留下死链，已加存在性检查与恢复测试。

复核指出整篇保留旧生成笔记会冻结将来的教材目录更新，因此改为双文件：系统课程页每次重新生成，`我的-*` 家长笔记只创建一次、以后绝不重写；先前导出的同名旧笔记保持原样并由新页面链接。知识库总览按钮现在由后台工作线程刷新首页再打开，避免安装新版后先看到缓存首页。

最终完整测试为 **718 passed, 1 skipped in 1636.68s**；PyInstaller 构建、dist 与已安装 EXE 的 `--smoke-check` 均 exit 0；打包课程 JSON 与源码 SHA-256 相同。SQLite 安装前备份及安装后完整性检查显示 11 份资料、112 道题，源库与备份 SHA-256 相同。桌面快捷方式的自定义照片图标已恢复。
