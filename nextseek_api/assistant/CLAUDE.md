# Working in `nextseek_api/assistant/`

The granular ops, their write gate and their invariants moved to `NessieAI/ns/`. Read
`NessieAI/ns/CLAUDE.md` before touching `CONTRACT.md` or the granular-op models in `models_api.py`.

## Invariants

These hold today and are covered by tests in `NessieAI/tests/api/`. Breaking one is a security or
data regression, not a refactor.

- **A task id is not a capability token.** `TaskProgressConsumer` demands an authenticated user and then ownership of the task, and refuses all three failure cases identically. Dropping either half turns a leaked UUID into another user's live progress stream. Its Origin check is defence in depth behind that, not instead of it.
- **Bundle history is merged under a row lock, never assigned over.** `DictSessionAdapter.save` re-reads the `ChatSession` row with `select_for_update` and folds bundles by id; its docstring gives the reason. A plain write deletes a concurrent turn's bundle, and the symptom is a follow-up question answered against the wrong result set.
- **Models here belong to the parent app's label and migration chain.** Each class declares `app_label = "nextseek_api"`, and its tables are created by migrations in `nextseek_api/migrations/`. A model without the label, or a migrations directory of this package's own, splits the chain and the next deploy stops on an unapplied dependency. Check the migration heads first (`nextseek_api/CLAUDE.md`).
- **A turn pass reaches only the allow table.** `TurnPassAuthentication` checks `turn_pass_allow.ALLOW_TABLE` inside `authenticate`, before any view runs, so a new route stays closed to a pass until it has a row there. The raw pass is never logged or stored (only its sha256 is), and a pass request never reaches the prod config (`is_turn_pass` on every prod swap). Tests: `nextseek_api/tests/test_turn_pass*.py`.
- **The granular-op request models refuse coercion.** `confirmed_write` is a strict boolean: the request-side half of the write gate's rule that only `True` confirms a write (`NessieAI/ns/CLAUDE.md`). Relaxing the model lets the string "true" reach a gate that is then the only defence.

## Landmines

- **The sidecar carries a hand copy of the granular-op models** (`NessieAI/docker/ns-sidecar/app/granular_models.py`), and nothing compares the two. Editing a model here drifts the sidecar silently, and the break shows up as a validation error in another container.
- **A deployment can turn the WebSocket off without touching this code.** `docker/scripts/entrypoint.sh` starts gunicorn (WSGI, no WebSocket) when `NEXTSEEK_SERVER=gunicorn`, and the chat panel absorbs that by polling. A report that "the progress socket is broken" is that setting until proven otherwise.
- **`models_db.py` is the HiBayes store as well as the chat's.** Most of its classes are `eval_*` tables, among them the spend reservation, and the `NessieAI/hibayes/` modules import them. Trimming a "chat" model file breaks the paid-run authorization store.
- **Every progress event rewrites the whole JSON column.** `make_db_event_callback` reads, appends and saves the full `progress` list per event, so a chatty turn is quadratic in written bytes and a slow turn shows as row contention rather than agent latency.
- **`excel_export.py` has callers on both sides of the boundary**: the assistant ViewSet, the admin project export (`nextseek_api/services/project_export.py`) and a lazy, guarded import from the NS orchestrator. It stays here as shared code; moving it into `NessieAI/` breaks the admin export.
- **A running instance is not this branch.** Only committed code from the deploy branch is deployed (`DEPLOYMENT.md` §1), and a patched container diverges from git. Behaviour seen on a deployed host is evidence about the image that host last built.

## Test command

See `NessieAI/tests/README.md` ("Django lane", with the `NessieAI/tests/api` and `NessieAI/tests/ns` areas).

## See also

- `nextseek_api/assistant/README.md`: what each module does, and the dependency map.
- `nextseek_api/assistant/CONTRACT.md`: the granular-op HTTP contract.
- `NessieAI/ns/CLAUDE.md`: the write gate and the granular-op invariants.
- `NessieAI/cc/CLAUDE.md`: the sandbox that writes transcripts into `models_db.py`.
- `nextseek_api/CLAUDE.md`: the migration chain and route registry rules of the parent app.
