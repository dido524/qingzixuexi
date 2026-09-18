# 增量学习报告验收记录

日期：2026-09-18（Asia/Shanghai）。本记录只使用正式资料库的 SQLite 副本和临时知识库目录；没有修改 `C:\晴子知识库\5th grade` 中的历史资料，也没有向打印机发送任务。

## 功能结论

- 孩子版和家长版分别生成，报告运行记录为不可变快照；再次生成会基于上一份快照计算增量。
- 三科数据按语文、数学、英语分别呈现；孩子版使用克制、可执行的表述，家长版保留样本数、趋势、错因和证据链接。
- 模型只负责受 Schema 和证据编号约束的文字改写；本地程序计算样本、趋势、优先级和增量。模型不可用或输出不合格时，自动使用本地模板。
- HTML 内含 A4 打印样式和显式“打印这份报告”按钮，只会打开系统/浏览器打印界面，不会静默打印。

## 正式数据副本验收

验收根目录：`C:\Users\Home\AppData\Local\Temp\qingzi-learning-report-acceptance-3e6ba0dc`。

- 报告编号：`report-acceptance-20260918-final`，状态 `completed`。
- 有效资料 5 份、有效题目 71 道、待确认 23 道、未纳入的低置信度判断 0 道。
- 快照同时包含语文、数学、英语三个科目，并包含 1 条模拟卷复测记录。
- 孩子版不包含家长证据明细；家长版能追踪到试卷编号、打印题号和回拍资料。
- 输出包括 `孩子版.html`、`家长版.html`、`最新学情报告.html` 和 `report.json`。

## 打印预览

使用 Chrome 无头打印生成本地 PDF，再用 Poppler 逐页渲染检查；PDF 只是验收中间产物。

- 孩子版：3 页，A4 纵向；标题、三科概况、重点练习、小目标和复测进展完整，无裁切或重叠。
- 家长版：18 页，A4 纵向；长知识点表按每 4 条拆为带表头的小表，列宽固定，长证据自动换行，所有记录完整可读；最后一页包含英语无样本提示和模拟卷复测追踪。
- 没有执行物理打印；安装后的页面仍由家长点击打印按钮后选择打印机。

## 自动化与分发

- 重基到 `origin/main` 后执行完整测试：`593 passed, 1 skipped in 416.97s`。唯一跳过项为 `tests/export/test_safe_write.py:176`，原因是本机没有 Windows symlink 创建权限。
- `scripts\build.ps1 -SkipTests` 构建成功；分发包 `--smoke-check` 退出码 0，报告 Schema、两份试卷 Schema、两份分析 Schema 和 `schema.sql` 均存在。
- 分发包和安装后 EXE 的 SHA-256 相同：`7F878D9DD0D2AE2E62A77A555E34242DDC6F4397A9AA656DCEF2901F500E0F2F`。
- 安装位置：`C:\Users\Home\Documents\QingziLearningAssistant\app\晴子学习助手.exe`。安装后烟测退出码 0，应用残留进程数 0。
- 桌面快捷方式 target、working directory 正确，图标保持 `C:\Users\Home\Documents\QingziLearningAssistant\assets\qingzi-photo.ico,0`。
- 安装前备份正式数据库到 `C:\Users\Home\AppData\Local\QingziLearningAssistant\backups\knowledge-before-reports-exams-20260918-091429.sqlite3`。
- 已重新生成 `C:\晴子知识库\5th grade\知识库首页.html`；页面包含学情报告、针对性模拟试卷和三科分类短板入口，用户可见内容不再显示英文 `declining`。

