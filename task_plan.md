# Task Plan: 晴子学习助手模型切换与 DeepSeek 备选

## Goal
在不削弱现有本地校验、家长确认和知识库安全边界的前提下，为晴子学习助手提供原生 Codex 与 DeepSeek 两种模型选择，并向用户暴露可安全保存的 DeepSeek API 配置界面。

## Current Phase
Phase 11: active-capture versus recovered-task review routing

## Phases

### Phase 1: Requirements & Discovery
- [x] Capture the requested user flow and safety boundary
- [x] Map the existing analysis, review, storage, and UI contracts
- [x] Record current constraints in findings.md
- **Status:** complete

### Phase 2: Architecture & Implementation Plan
- [x] Write and self-review the design spec
- [x] Write the TDD implementation plan
- [x] Lock file ownership and interfaces
- **Status:** complete

### Phase 3: Coordinate OCR & Annotation
- [x] Add schema/domain support with strict coordinate validation
- [x] Render immutable annotated image copies for every page
- [x] Keep original scans unchanged
- **Status:** complete

### Phase 4: Confirmation & Knowledge Publication
- [x] Present annotated pages and question-level corrections for review
- [x] Publish confirmed errors only
- [x] Support one same-subject batch containing multiple scanned pages
- **Status:** complete

### Phase 5: Verification & Delivery
- [x] Run focused and full regression suites
- [x] Build, install, preserve custom desktop icon, and smoke-test
- [x] Commit and push the exact source tree to GitHub main
- **Status:** complete

### Phase 6: Provider Contract & Configuration
- [x] Define the two-provider configuration contract and Windows-protected API-key storage
- [x] Add a DeepSeek HTTP client with strict JSON validation and image support
- [x] Preserve Codex as the default and zero-configuration path
- **Status:** complete

### Phase 7: Runtime Routing
- [x] Route homework analysis through the selected provider
- [x] Route report narrative, exam generation, and exam verification consistently
- [x] Keep workflow/database/publication behavior provider-neutral
- **Status:** complete

### Phase 8: User Interface
- [x] Add a compact model selector to the main window
- [x] Add a model configuration dialog for endpoint, model name, and API key
- [x] Validate configuration without exposing the secret in logs or files
- **Status:** complete

### Phase 9: Verification & Delivery
- [x] Run focused provider/config/UI tests and the full regression suite
- [x] Build, back up the database, install, preserve the custom icon, and smoke-test
- [x] Commit and push the verified source tree to GitHub main
- **Status:** complete

### Phase 10: DeepSeek Real-Homework Response Diagnosis
- [x] Locate the failed session and capture a safe validation-error category without exposing student content or the API key
- [x] Reproduce the contract mismatch against the existing captured evidence without changing the knowledge base
- [x] Add a failing regression test, implement the smallest fix, and verify it
- [x] Rebuild/install safely, test the installed app, and push the fix to GitHub
- **Status:** complete

### Phase 11: Active-Capture Review Routing
- [x] Identify the newest captured document and whether analysis/review data was actually saved
- [x] Reproduce why both “本次分析 · 待确认” and “待家长确认” open an older page
- [x] Add failing UI/review regression tests and repair the routing without weakening per-question confirmation
- [x] Run full verification, rebuild/install safely, preserve user data/icon, and push to GitHub
- **Status:** complete

## Key Questions
1. How does the current model schema represent OCR text, question status, and page identity?
2. Where can confirmation gate knowledge publication without duplicating review state?
3. How can annotations remain accurate across arbitrary scan dimensions while preserving originals?
4. Which output and UI affordances make multi-page review practical?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use normalized image coordinates in model JSON | Resolution-independent, validates cleanly, and supports local rendering without sending edited images back to the model |
| Render annotations locally into separate image files | Keeps original evidence immutable and makes output deterministic and printable |
| Require confirmation before unmarked incorrect/partial answers affect knowledge stats | Prevents model-only grading errors from polluting long-term learning evidence |
| Treat one capture session as one same-subject multi-page document | Existing continuous capture already supplies ordered pages and matches the requested batch behavior |
| Reuse existing buttons | 本次分析 opens the grading gallery; 待家长确认 performs approval without adding UI clutter |
| Support exactly `codex` and `deepseek` | Matches the requested scope and avoids premature provider-framework complexity |
| Keep prompts and schemas provider-neutral | Enables both backends to produce the same validated domain objects |
| Store the DeepSeek key with Windows DPAPI | Keeps the secret out of JSON, SQLite, logs, chat, and Git |
| Use `deepseek-flash` as the editable default | Current official DeepSeek API documentation identifies it as the image-capable OpenAI-compatible model |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `rg` wildcard path for `schema/*.json` was invalid under Windows argument handling | 1 | Switched to explicit schema file paths |
| Planning update patch used template-only context absent from the created file | 1 | Reapplied with actual file context |
| Combined Markdown/UI inspection produced no output | 1 | Split into explicit reads instead of repeating the same command |
| PowerShell `Get-ChildItem -Name` received an array in the filter position | 1 | Replaced with explicit `Test-Path` checks |
| Repository has no `requirements.txt` | 1 | Use `pyproject.toml` and `requirements-build.txt` as the dependency sources |
| PowerShell parsed a quoted regex as code in a combined security-check command | 1 | Split the checks into separate commands with simple quoting |
| Full pytest run hit a native access violation at 96% | 1 | Traced it to pointer-width-unsafe ctypes defaults; declared exact DPAPI and LocalFree ABIs, then passed 1,000-cycle stress/Tk and full regression |
| Focused regression command could not import source package because the worktree virtual environment does not install this worktree editable by default | 1 | Rerun with `PYTHONPATH=src` before interpreting the test result |
| A compound PowerShell install command exited immediately after the installer, before restoring the custom icon | 1 | Restored icon in a separate command and verified shortcut target/icon and installed smoke check |

## Notes
- The user previously authorized implementation without plan-by-plan confirmation; proceed after design self-review.
- Do not infer handwritten student identity or include it in OCR output.
