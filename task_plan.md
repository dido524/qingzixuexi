# Task Plan: OCR 批改影像与错题确认入库

## Goal
为晴子学习助手增加未批改作业的多页同科 OCR 判题、原图批改影像、家长确认错题和确认后入库的完整流程。

## Current Phase
Phase 5

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
- [ ] Commit and push the exact source tree to GitHub main
- **Status:** in_progress

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

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `rg` wildcard path for `schema/*.json` was invalid under Windows argument handling | 1 | Switched to explicit schema file paths |
| Planning update patch used template-only context absent from the created file | 1 | Reapplied with actual file context |
| Combined Markdown/UI inspection produced no output | 1 | Split into explicit reads instead of repeating the same command |

## Notes
- The user previously authorized implementation without plan-by-plan confirmation; proceed after design self-review.
- Do not infer handwritten student identity or include it in OCR output.
