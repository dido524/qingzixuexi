# Findings & Decisions

## Requirements
- Detect an unmarked worksheet and use the existing vision-capable model flow for OCR and grading.
- Return annotated copies based on the original scanned images.
- Show question correctness, corrections, and reference answers on those copies.
- Require parent confirmation before incorrect/partial model judgments enter the knowledge base.
- Support multiple images/pages of the same subject in one scan session.
- Preserve the existing teacher-mark priority and mixed-subject split behavior.

## Research Findings
- Prior local guidance identifies coordinate-bearing JSON as the controlled boundary for OCR-to-image annotation.
- The application already analyzes multiple durable page images in one Codex CLI request.
- `AnalysisResult` already distinguishes `teacher_marked`, `auto_grade`, and `mixed`; unmarked work already reaches model grading as `auto_grade`.
- Each `QuestionAnalysis` already carries page number, OCR summaries, student/reference answers, status, decision source, knowledge points, confidence, and reason; the missing contract is only the page-relative answer location needed for rendering.
- The strict transport and domain schemas reject extra fields, so a new coordinate field must be added to both schemas and to the typed conversion path.
- Current mastery eligibility accepts non-review questions at confidence >= 0.80; this is too permissive for the new requirement because unconfirmed model-only errors could affect the knowledge base.
- The controller currently persists analysis and immediately recomputes knowledge statistics before publication; the confirmation gate must therefore be enforced in the repository's effective-question layer, not only in the UI.
- Existing `review_decisions` and `effective_questions` already provide a parent overlay with audit history and re-publication. Reusing this mechanism is safer than creating a second confirmation subsystem.
- Best fit: persist the model's original `incorrect`/`partial` status plus a `requires_parent_confirmation` flag. The `effective_questions` view exposes such unreviewed rows as `needs_review`; once a parent decision exists it exposes the confirmed status. This preserves the model proposal for the annotated image and review screen while preventing premature knowledge statistics.
- `ReviewDialog` already supports question-by-question parent confirmation and source-image preview, so it can be extended to prefer the annotated page and offer both annotated and original image actions.
- No explicit schema migration framework exists; `schema.sql` is replayed with `CREATE ... IF NOT EXISTS`. Avoiding a new column prevents upgrade hazards on the user's existing SQLite database.
- A migration-free confirmation gate is possible directly in `effective_questions`: when no parent review exists and raw `decision_source='model'` with raw status `incorrect` or `partial`, expose effective status `needs_review`. Raw `questions.status` remains the model proposal for review and annotation.
- Rebuilding `effective_questions` with `DROP VIEW IF EXISTS` plus `CREATE VIEW` on repository startup is safe because it is a derived read view and carries no data.
- Image coordinates do not need database persistence if annotated images are rendered after archival and stored durably beside the archived originals; review can derive the annotated filename from the trusted source path.
- Normal same-subject batches archive every page into one document directory while preserving original page numbers. Mixed-subject batches create child directories and child analyses with the same original page numbers, so the annotation renderer can use one page-number keyed contract in both paths.
- The controller's durable outcome currently exposes archived originals, analysis Markdown, and dashboard paths. A grading gallery path or annotated-page tuple must be added if the main UI is to return the marked images immediately.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Normalized `{x, y, width, height}` boxes in the transport schema | Independent of page pixels, compatible with strict structured output, and straightforward to validate against `[0,1]` bounds |
| Pillow-based overlay renderer | Already packaged, deterministic, offline, and suitable for Unicode labels when a bundled/system font is selected |
| Annotated outputs live beside analysis artifacts, not source scans | Evidence immutability and simple open/print actions |
| Reuse the existing review overlay and audit trail | Avoids duplicate state machines and keeps parent decisions authoritative |
| Store original model status plus a confirmation-required flag | The image can display the model proposal while effective knowledge facts stay pending |
| Derive confirmation need in `effective_questions` instead of adding a DB column | Keeps existing database upgrades migration-free while preserving raw model status |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| PowerShell `rg` rejected a wildcard path passed literally for schema JSON | Read the two schema files explicitly and record the error so it is not repeated |
| First planning-file patch expected a placeholder issue row that the actual file did not contain | Retried with the exact existing table context |
| Combined Markdown/UI inspection command returned no output | Split the inspection into smaller explicit file reads |

## Resources
- User explicitly prefers no new button: reuse 本次分析 for the grading gallery and 待家长确认 for approval.
- `src/qingzi_learning/analysis/codex_cli.py`
- `src/qingzi_learning/schema/analysis-transport.schema.json`
- `src/qingzi_learning/schema/analysis-result.schema.json`
- `src/qingzi_learning/review/service.py`
- `src/qingzi_learning/workflow/controller.py`

## Visual/Browser Findings
- No external visual source used yet.

## Model Switching Findings (2026-09-19)
- The current runtime is already contract-oriented: Codex receives one fixed prompt plus images and must return the packaged strict analysis schema.
- The official DeepSeek API now documents native image input for `deepseek-flash` over an OpenAI-compatible `https://api.deepseek.com` endpoint, including base64 image data URLs.
- DeepSeek JSON Output guarantees syntactically valid JSON but not this application's full schema, so the existing local JSON Schema and typed validation must remain authoritative.
- Codex `exec --output-schema` remains the native default and reuses the locally authenticated ChatGPT account.
- The application currently has no settings UI or durable user configuration beyond fixed `AppConfig` paths.
- `pyproject.toml` does not include an HTTP client dependency; the safest small implementation is the Python standard library HTTPS client so the packaged app gains no new runtime dependency.
- The model selector must affect newly submitted work only. A running worker owns its controller for the process lifetime, so selection changes need a provider router that reads a thread-safe settings snapshot at each model call rather than reconstructing the worker.
- The DeepSeek API key must never be written to the ordinary settings JSON. On Windows, DPAPI-protected bytes can be stored under the application data directory and decrypted only for the current user.
- DeepSeek task adapters can reuse the existing prompts and local validators, so provider choice does not alter review, knowledge, or publication semantics.
- Provider operational failures must not enter the exam quality-repair loop; doing so can duplicate paid requests after ambiguous network failures. Only malformed/schema-invalid exam output is eligible for the existing one-repair pass.
- API keys must be restricted to printable non-whitespace ASCII and transport exceptions must be converted to safe application codes so header-validation errors cannot echo secrets.
- ctypes defaults are not pointer-safe for `LocalFree` on 64-bit Windows. CryptProtectData, CryptUnprotectData, and LocalFree require explicit `argtypes` and `restype`; Microsoft requires DPAPI output buffers to be released with LocalFree.
- The installed Microsoft Store Python interpreter virtualizes some direct AppData access. User-database backup checks must therefore rely on the actual Windows filesystem view (application closed, no WAL/SHM, matching file hash) rather than opening that path through the Store interpreter.
