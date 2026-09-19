# Codex / DeepSeek Model Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe two-choice runtime model selector and user-editable DeepSeek configuration to the installed Windows learning assistant.

**Architecture:** A thread-safe settings manager owns non-secret JSON and a DPAPI-protected key. Narrow routers preserve the existing analyzer, narrative, generator, and verifier interfaces while selecting either the existing Codex implementations or new DeepSeek adapters at task start.

**Tech Stack:** Python 3.13, Tkinter, urllib HTTPS, ctypes Windows DPAPI, JSON Schema, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-19-codex-deepseek-switch-design.md`

## Global Constraints

- User-facing choices are exactly `codex` and `deepseek`.
- Codex remains the default and requires no API key configuration.
- The DeepSeek key never enters ordinary settings JSON, SQLite, logs, exceptions, or Git.
- DeepSeek uses HTTPS and defaults to `https://api.deepseek.com` plus `deepseek-flash`.
- Existing local schemas, parent review, immutable originals, and confirmed-only knowledge publication remain authoritative.
- No live paid DeepSeek call is made without a user-supplied key.

---

### Task 1: Secure Settings Contract

**Files:**
- Create: `src/qingzi_learning/models/settings.py`
- Create: `src/qingzi_learning/models/__init__.py`
- Create: `tests/models/test_settings.py`

**Interfaces:**
- Produces: `ModelSettings`, `ModelSettingsManager.snapshot()`, `save(...)`, `has_deepseek_key()`, and `deepseek_key()`.
- Consumes: `AppConfig.app_data_root` and an injectable secret-store protocol.

- [x] Write failing tests for defaults, JSON persistence, validation, key preservation, key clearing, and absence of plaintext secrets.
- [x] Run `pytest -q tests/models/test_settings.py` and confirm failure because the module is absent.
- [x] Implement immutable settings, atomic JSON writes, thread locking, and Windows DPAPI secret storage.
- [x] Run the focused settings tests and confirm they pass.

### Task 2: DeepSeek Structured Client and Task Adapters

**Files:**
- Create: `src/qingzi_learning/models/deepseek.py`
- Create: `tests/models/test_deepseek.py`
- Modify: `src/qingzi_learning/exams/generator.py`

**Interfaces:**
- Produces: `DeepSeekClient.complete_json(prompt, images=(), reasoning_effort=...)`, `DeepSeekAnalyzer`, `DeepSeekNarrativeProvider`, `DeepSeekExamGenerator`, and `DeepSeekExamVerifier`.
- Consumes: the settings manager, existing prompts, schemas, `_from_payload`, `validate_result`, and narrative validation.

- [x] Write failing transport tests covering authorization, base64 image blocks, JSON Output, model selection, sanitized HTTP errors, timeouts, and invalid JSON.
- [x] Write failing adapter tests showing that DeepSeek output passes the same analysis/narrative/exam validators as Codex output.
- [x] Run focused tests and confirm expected failures.
- [x] Implement the standard-library HTTPS transport and four adapters with no external network call in tests.
- [x] Run focused tests and confirm they pass.

### Task 3: Two-Provider Runtime Routing

**Files:**
- Create: `src/qingzi_learning/models/router.py`
- Create: `tests/models/test_router.py`
- Modify: `src/qingzi_learning/workflow/controller.py`
- Modify: `src/qingzi_learning/main.py`

**Interfaces:**
- Produces: `ModelServices` containing routed analyzer, narrative provider, generator, and verifier.
- Consumes: one shared `ModelSettingsManager`, existing Codex implementations, and DeepSeek adapters.

- [x] Write failing tests proving each task reaches only the selected implementation and a setting change affects the next call.
- [x] Run router tests and confirm failure.
- [x] Implement narrow routers and inject them through application composition without changing repository ownership.
- [x] Run router, workflow, reporting, and exam service tests.

### Task 4: Model Settings UI

**Files:**
- Create: `src/qingzi_learning/ui/model_settings_dialog.py`
- Create: `tests/ui/test_model_settings_dialog.py`
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `tests/ui/test_view_model.py`

**Interfaces:**
- Produces: a modal settings dialog and current-model indicator.
- Consumes: `ModelSettingsManager` and main-window busy state.

- [x] Write failing Tk tests for current-model display, Codex selection, DeepSeek field validation, masked/key-preserving save, clearing, and busy-state blocking.
- [x] Run focused UI tests and confirm failure.
- [x] Implement the compact header control and modal dialog.
- [x] Run focused UI tests and confirm they pass.

### Task 5: Errors, Packaging, and Delivery

**Files:**
- Modify: `src/qingzi_learning/ui/app.py`
- Modify: `src/qingzi_learning/main.py`
- Modify: `tests/test_packaging_assets.py`
- Modify: `task_plan.md`, `progress.md`, `findings.md`

**Interfaces:**
- Consumes: provider-specific sanitized error codes.
- Produces: installed executable with Codex/DeepSeek configuration and unchanged desktop icon.

- [x] Add failing tests for user-facing missing-key, authentication, rate-limit, and network messages.
- [x] Implement sanitized mappings and smoke-check behavior that requires Codex only when Codex is selected.
- [x] Run the complete pytest suite and `git diff --check`.
- [x] Build with `scripts/build.ps1 -SkipTests`, back up the database, install, restore the custom icon, and run installed `--smoke-check`.
- [x] Commit, push the exact verified tree to GitHub main, and record the commit and backup path.
