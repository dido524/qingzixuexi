# All-Question Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every new model-graded question individually confirmable and make new-capture buttons open the correct task.

**Architecture:** Store new model judgments as pending until parent confirmation, using the existing effective-question overlay. Thread the selected capture scope through the existing review worker and dialog. Keep legacy correct records unchanged, but expose them when their task is explicitly opened for review.

**Tech Stack:** Python, SQLite, Tkinter, pytest, PyInstaller, PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-19-all-question-review.md`

## Global Constraints

- Preserve original scans, existing reviews, knowledge database, and teacher-evidence priority.
- Reuse existing buttons/dialog; no new provider or model call.
- Install on Windows with hidden launcher behavior and preserve the custom desktop icon.

## Review Focus

- All-correct new capture: five correct model questions must remain pending and not count as mastery before review.
- Mixed teacher/model capture: teacher judgments remain effective while model judgments await confirmation.
- Selected old task: its review list must exclude the new and other old tasks.
- Split task: review must include its children, not unrelated documents.
- Saving one question: the dialog must advance within the same task, not jump to an older pending task.

---

### Task 1: Persist every new model judgment as pending

**Files:** `src/qingzi_learning/storage/repository.py`, `src/qingzi_learning/storage/schema.sql`, `tests/review/test_review_service.py`

**Interfaces:** `KnowledgeRepository.pending_review_ids(document_ids=None)` returns `(document_id, question_id)` pairs; `ReviewService.list_pending(document_ids=None)` returns `ReviewItem` objects.

- [x] Write integration tests for new all-correct model analysis, teacher priority, and legacy correct preservation.
- [x] Run targeted tests to see the expected failures.
- [x] Mark new desktop model questions pending irrespective of proposed status, and let the effective view gate them.
- [x] Run targeted tests; commit with the verified release.

### Task 2: Scope the existing dialog and latest-result routing

**Files:** `src/qingzi_learning/ui/app.py`, `src/qingzi_learning/review/service.py`, `src/qingzi_learning/storage/repository.py`, `tests/review/test_review_ui.py`, `tests/ui/test_view_model.py`

**Interfaces:** `WorkflowWorker.submit(..., review_scope_id=None)` carries the root task ID; the worker resolves child document IDs and reads only that scope.

- [x] Write failing UI/worker tests for latest selection, old task isolation, split child inclusion, and next-question continuity.
- [x] Run targeted tests to see the expected failures.
- [x] Route the current capture by default, preserve explicit old selection, and keep review scope across save/conflict.
- [x] Run targeted tests; commit with the verified release.

### Task 3: Ship and verify

**Files:** package output and desktop installation; documentation notes.

- [x] Run full test suite and build; check changed-file diff and smoke test.
- [x] Confirm app closed, locate/backup actual user database, install app, preserve icon.
- [x] Test installed executable and push verified branch to `origin/main`.
