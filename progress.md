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
