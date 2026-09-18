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
