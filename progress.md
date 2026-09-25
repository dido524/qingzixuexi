# Progress Log

## Session: 2026-09-18

### Phase 1: Requirements & Discovery
- **Status:** in_progress
- **Started:** 2026-09-18
- Actions taken:
  - Captured the OCR grading, annotated-image, confirmation, and multi-page requirements.
  - Selected coordinate-bearing model JSON and immutable local rendering as the initial architecture.
  - Started mapping the existing pipeline before changing production code.
  - Confirmed that unmarked work already uses `auto_grade`; question OCR and judgment fields exist, but image coordinates and a model-error confirmation gate do not.
  - Traced knowledge publication and confirmed it currently applies model judgments immediately.
  - Chose to extend the existing effective-question/review overlay so model-only errors remain pending until parent confirmation.
  - Verified that this can be done without a risky database-column migration by changing only the derived effective view.
  - Confirmed that same-subject and mixed-subject archive paths both preserve original page numbers, allowing one annotation renderer contract.
- Files created/modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-09-18 | `rg` wildcard schema path invalid on Windows | 1 | Used explicit JSON paths |
| 2026-09-18 | Planning patch context mismatch | 1 | Retried against the actual file content |
| 2026-09-18 | Combined inspection command produced no output | 1 | Split into smaller explicit reads |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 1 discovery |
| Where am I going? | Spec, TDD implementation, verification, install, and push |
| What's the goal? | Multi-page OCR grading with annotated images and confirmed-error publication |
| What have I learned? | Coordinate-bearing JSON plus local overlay is the safest boundary |
| What have I done? | Implemented and regression-tested coordinate OCR, local annotations, review gating, and reused UI actions |

### Phase 3-4: OCR, Annotation, and Confirmation
- **Status:** complete
- Added strict coordinate-bearing transport output with legacy cached-analysis compatibility.
- Added immutable per-page PNG annotations and a printable multi-page HTML gallery.
- Added `model_pending` storage semantics so new auto-graded errors remain outside mastery/error statistics until parent confirmation.
- Reused “本次分析” for the grading gallery and “待家长确认” for approval.
- Confirmed historical jobs without coordinate OCR remain replayable.

## Latest Test Result
- Focused review, UI, storage, schema, and annotation regression: `230 passed`.
- Full regression: `613 passed, 1 skipped`.
- Packaged EXE build completed; installed smoke check exited `0`.
- Pre-install database backup: `C:\Users\Home\AppData\Local\QingziLearningAssistant\backups\knowledge-before-ocr-grading-20260918-210553.sqlite3`.
- Desktop shortcut continues to use `C:\Users\Home\Documents\QingziLearningAssistant\assets\qingzi-photo.ico`.
- GitHub `main` received the exact verified source tree in commit `93504a49c3d526b160c6855f7b11a739aadaef0d`.

## Session: 2026-09-19 — Codex / DeepSeek Model Switching

### Phase 6: Provider Contract & Configuration
- **Status:** in_progress
- Confirmed from current official documentation that DeepSeek `deepseek-flash` accepts image inputs through the OpenAI-compatible API.
- Chose exactly two user-facing providers: native Codex and DeepSeek.
- Chose Windows DPAPI for the DeepSeek API key and ordinary local JSON only for non-secret settings.
- Chose a runtime router so changing the selection applies to subsequent operations without restarting the application.

## Error Log Additions
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-09-19 | `Get-ChildItem -Name` treated the filename array as a filter value | 1 | Used explicit `Test-Path` checks |
| 2026-09-19 | `requirements.txt` was absent | 1 | Read dependency declarations from `pyproject.toml` |
| 2026-09-19 | Combined PowerShell secret scan had unsafe nested quoting and failed to parse | 1 | Split diff, secret scan, and DPAPI checks into independent commands |
| 2026-09-19 | Full suite exited with a Windows native access violation at 96% | 1 | Added explicit 64-bit ctypes signatures for CryptProtectData, CryptUnprotectData, and LocalFree; 1,000-cycle DPAPI/Tk stress passed |

### Phases 6-8: Model Configuration, Routing, and UI
- **Status:** complete
- Added the exact `codex` / `deepseek` settings contract with Codex as the default.
- Added Windows current-user DPAPI storage for the DeepSeek API key; ordinary JSON contains only provider, endpoint, and model.
- Added DeepSeek image analysis, report narrative, exam generation, and independent exam verification adapters using the existing prompts and local validators.
- Added runtime routers so a saved selection applies to the next model-backed task without restarting.
- Added the main-window current-model indicator and model-settings dialog with endpoint, model name, masked key entry, preservation, replacement, and explicit clearing.
- Added sanitized missing-key, authentication, rate-limit, network, and malformed-response messages.
- Independent review found and verified fixes for conflicting key actions, malformed keys, duplicate provider retries, corrupt-secret recovery, and 64-bit native memory signatures.

## Latest Model-Switch Verification
- Focused post-review regression: `60 passed`.
- DPAPI stress: 1,000 protect/unprotect cycles followed by Tk startup completed successfully.
- Final full regression after native ABI fix: `668 passed, 1 skipped` in `351.38s`.
- `git diff --check`: exit `0` (line-ending notices only).
- PyInstaller desktop build completed successfully.
- Pre-install database backup: `C:\Users\Home\AppData\Local\QingziLearningAssistant\backups\knowledge-before-model-switch-20260919-094133.sqlite3`; source and backup SHA-256 both `712F7F72C4F973DB5D6DC7E2645D562412332ED06F800F7701145611FFF3002F`.
- Installed EXE: `C:\Users\Home\Documents\QingziLearningAssistant\app\晴子学习助手.exe`, version `0.1.0`; installed smoke check exited `0`.
- Desktop shortcut target/working directory were verified and the custom `qingzi-photo.ico` icon was restored.
- The Microsoft Store Python interpreter virtualizes direct AppData access, so real-user database verification used closed-file/no-WAL state plus byte-identical SHA-256 rather than opening the live path through that interpreter.
- GitHub `main` received the exact verified code tree in commit `894a49b260997a19f68ee3949e55263d2b1d8490` (tree `83e939ac92e3c5a81468656e571821e156617fc5`).

## Delivery Error Log Additions
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-09-19 | Store Python could not open the real AppData SQLite path and reported a virtualized size | 1 | Stopped using that interpreter for real user-data inspection; verified the closed source and copied backup by PowerShell SHA-256 with no WAL/SHM present |

## Session: 2026-09-19 — DeepSeek Real-Homework Response Failure
- Screenshot shows the application mapping `invalid_deepseek_response` to “DeepSeek 返回的分析格式不完整，资料已保留，请重试”.
- Earlier live checks proved text JSON and a synthetic digit image worked; they did not exercise the full homework schema.
- Current phase: locate the exact schema/domain failure, reproduce safely, then test and repair.
- Located the two latest failed English one-page sessions; verified their original images remain in `英语/待处理` and no DeepSeek raw response is currently stored.
- Replayed the latest archived image once with the current DeepSeek request: 11 questions returned, but root schema failed on unexpected `grade` and missing required fields.
- Replayed the same image once with the packaged transport schema explicitly appended: 11 questions returned, full schema and domain validation passed.
- Root cause confirmed at the prompt-to-provider boundary; next: test-first single-point repair in `DeepSeekAnalyzer`.
- Added a regression assertion that the complete packaged analysis transport schema is present in DeepSeek's request. It failed on the old behavior, then passed after the focused analyzer patch.
- Full suite on the patched tree: `668 passed, 1 skipped in 424.83s`.
- Real `DeepSeekAnalyzer.analyze` on the same archived image: one page, 11 questions, all answer bounding boxes present; no repository/database call.
- Closed app confirmed before backup/install. Database backup: `C:\Users\Home\AppData\Local\QingziLearningAssistant\backups\knowledge-before-deepseek-schema-fix-20260919-103631.sqlite3`; source and copy SHA-256 both `712F7F72C4F973DB5D6DC7E2645D562412332ED06F800F7701145611FFF3002F`.
- Rebuilt PyInstaller package, installed into the existing `C:\Users\Home\Documents\QingziLearningAssistant\app`, restored custom desktop icon, and confirmed installed `--smoke-check` exit `0`.
- Independent read-only review found no blocking issue; it noted that single-page live validation cannot prove all future/multi-page outputs conform.
- Pushed the verified repair commit `0aa77f4988b6667c9d30b0e628e6ff8c29c98254` to GitHub `main` by fast-forward.

## Session: 2026-09-19 — New Capture Opens Old Review Page
- User reports that after completing a new capture, both “本次分析 · 待确认” and “待家长确认” still show the previously failed/older image rather than the new page, preventing per-question confirmation of the new work.
- App is currently running; diagnostics must remain read-only until the active job and UI routing are understood.
- Located latest completed math capture `capture-4da51a7ae4f947a6807787c76debea1e` (five model judgments marked correct) and prior math capture `capture-5b49340663744f3f8c860556c083a49d` (needs review).
- Traced `本次分析` through selected recovered-task completion and `待家长确认` through a global, unfiltered pending-review query; this can surface the older task even immediately after the new capture.
- User confirmed all model-graded questions, including correct ones, require individual parent confirmation. Added red-then-green tests for all-correct gating, teacher priority, task scope, split children, latest active capture, restart recovery, and explicit status wording.
- Initial full suite exposed 65 legacy-expectation failures. Introduced a production-only all-question policy flag (`load_config=True`, custom/test `AppConfig=False`) so existing historical workflows remain compatible; second full run reached 674 passes and one fake-repository interface failure, which was fixed.
- The application was closed before installing; verified live database backup with 11 documents and five latest questions at `C:\Users\Home\Documents\QingziLearningAssistant\backups\knowledge-pre-all-question-review-20260919.sqlite3`.
- Fresh-context review found two Important restart defects. Added five failing cases, then fixed chronological root-task selection (including same-second inserts), pending-task default selection, split-child exclusion, and corrupt-journal isolation; targeted restart/split suite now passes `10 passed`.
- Final exact-tree full suite: `684 passed, 1 skipped in 545.96s`; `git diff --check` exit 0 (only Windows line-ending notices).
- PyInstaller package built successfully and installed to `C:\Users\Home\Documents\QingziLearningAssistant\app\晴子学习助手.exe` with the app closed. Restored the desktop shortcut's `qingzi-photo.ico` icon; installed `--smoke-check` exited 0.
- Pushed release code commit `74c7aff36e5ceef1a018494a81a9d71ad8e6f199` to GitHub `main` by fast-forward; verified the remote ref matches.

## Session: 2026-09-19 — 2024 BNU Curriculum Graph

- Identified and visually inspected the newest three Download images; photographed volume is BNU grade-5 upper mathematics, 2024-approved edition. Transcribed eight numbered units, two integrated-practice entries, one math-play entry, and review with page anchors into findings.md.
- Confirmed the older existing planning files were all complete through Phase 11. Added Phase 12 for catalog, extensibility, visualization, integration, and verification.
- Web cross-check found edition drift in third-party indexed curricula; user photographs remain authoritative for the first catalog version.
- Added the versioned course catalog, offline HTML/Obsidian-compatible graph, automatic overview entry, and editable parent notes separate from regenerated system notes. Preserved older generated notes and all pupil facts.
- Review caught a missing-parent-note repair gap. A red/green test now confirms deleted notes are recreated while surviving parent edits remain untouched; 28 affected publication cases passed.
- Final full suite: `718 passed, 1 skipped in 1636.68s`. PyInstaller build passed; source and bundled catalog SHA-256 match (`EBD6F41019380E3721DD5B3B0F174CF93CAAE095695C3E949119C0FDB5D4E571`); dist and installed `--smoke-check` both exited 0. Dist EXE SHA-256: `2C7EADFC38FE3E0A5B641AC4CE10EA337BC17A3301BA05F2D735AF9C3E6E648F`.
- With the application closed, backed up the real SQLite file to `C:\Users\Home\AppData\Local\QingziLearningAssistant\backups\knowledge-before-curriculum-20260919-181146.sqlite3`. Source/backup SHA-256: `8706BDBC0B5D33C0EF0462A4B8416DB68BB6AF0A8E8ED0D2D3049A3E83BE3477`; integrity `ok`, 11 documents, 112 questions. Installed in the existing application directory, restored the custom desktop icon, and verified the installed EXE hash equals dist and the database hash/counts are unchanged.
- The source photos and their private knowledge-base copies match byte-for-byte by SHA-256; none enter the public repository. Current catalog covers photographed contents only, not invented inner-chapter concepts. Future grade/club catalogs use separate IDs and tracks, but migration from the currently fixed `5th grade` root will be planned when actually needed.

## Session: 2026-09-25 — Complete Per-Question Parent Review

- Traced the one-question-only symptom to singleton Tk variables in `ReviewDialog`; all pending question records are present in SQLite.
- Selected a bounded UI redesign: retain the shared large image preview, render a full three-section card for every question, keep per-question saves and explicit batch confirmation for AI-correct questions.
- Live read-only check found nine model-pending questions; existing reason text ranges from missing-evidence notices to worked mathematical explanations.
- Next step: add failing UI/prompt tests before changing production code.
- Logged one planning patch context error and corrected it without changing product code.
- Added three regression tests for full per-question cards, independent card saving, and detailed model-reason instructions.
- RED verified: all three fail for the intended missing behavior (`review_cards` absent and prompt wording absent), not from test setup errors.
- First GREEN run passed the full-card and prompt cases. The independent-save fixture omitted `review_all_model_questions=True`, so its model-error row was correctly excluded by the existing mixed-mode safety policy; fixed the fixture rather than weakening production filtering.
- Implemented one complete scrollable review card per pending question, shared-preview selection, independent per-card saves, and retained explicit batch confirmation for AI-correct questions.
- Added “正确做法” composition for historical analyses and richer 2–4 sentence reason instructions shared by Codex and DeepSeek.
- Focused review/model regression: `75 passed`; no Tk variable-release warnings remained.
- Generated and inspected local top/scrolled screenshots from live pending data; card hierarchy, wrapping, per-card controls, and fixed footer were readable at 1180×900.
- Added a DeepSeek integration assertion proving it receives the shared detailed-reason instruction.
- `git diff --check` and source compilation passed; focused review/Codex/DeepSeek regression remains `75 passed`.
- Final pre-delivery full regression on the current product tree: `772 passed, 1 skipped in 1511.58s`; exit code `0`.
- Confirmed the desktop app was closed, then backed up the live database and model settings to `C:\Users\Home\Documents\QingziLearningAssistant\backups\20260925-100759-per-question-review-cards`; source and backup both report `PRAGMA integrity_check = ok` and identical counts across every user table.
- Rebuilt the PyInstaller package and installed it to `C:\Users\Home\AppData\Local\QingziLearningAssistant\app\晴子学习助手.exe`.
- Installed EXE and build EXE SHA-256 both equal `BAEC5F43A67C05662FF81E1651226ECE67D8CB92C83803F07716AE28A2D5230B`; desktop shortcut target matches, installed `--smoke-check` exited `0`, and the live database still has 23 documents, 39 pages, and 248 questions with integrity `ok`.
