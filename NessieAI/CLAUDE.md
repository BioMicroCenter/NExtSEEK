# Working in NessieAI/

Rules that span more than one unit. Each unit's own rules are in its `CLAUDE.md` (list at the end).
Test commands live only in `NessieAI/tests/README.md`.

## Boundary

- NessieAI declares no models, migrations, AppConfig or app label. Engine code that needs the ORM imports `nextseek_api.assistant.models_db` directly and runs only inside a configured Django process.
- Allowed back-edges into the API side, and no others:
  - `nextseek_api.assistant.models_db`, from engine modules that persist (for example `NessieAI/cc/cc_transcript_store.py`, `NessieAI/cc/turn.py`, `NessieAI/router/turn_ledger.py`, the `NessieAI/hibayes/` ORM modules)
  - `nextseek_api.batch_upload.helpers`, from `NessieAI/ns/reingest_qa.py`
  - `nextseek_api.assistant.session_adapter` (for `SessionSaveError`), from `NessieAI/ns/turn.py`
  - `nextseek_api.assistant.models_evaluator` (the retry-context response models) and `models_db` (`QueryTask` reads), from `NessieAI/ns/retry.py`
  - `seek.seekdb`, lazily, from `NessieAI/cc/cc_provision.py`
  - `nextseek_api.models`, from `NessieAI/schema_rag/`
  - `nextseek_api.assistant.excel_export`, lazily and behind a guard, from `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`
  - `nextseek_api.conftest` (its fixtures), from `NessieAI/tests/nessie_tests/tests_container/`
- No engine module imports `nextseek_api.services`: that would be the API calling itself through the engine. The ViewSets there call the engine (`NessieAI/router/policy.py`, `NessieAI/cc/turn.py`, `NessieAI/ns/{turn,artifacts,retry}.py`) and hand in the host seams (the session adapter, the event callback, the SEEK credentials). `policy.py` and `artifacts.py` have no back-edge at all.
- Importing the router does not load `NessieAI/hibayes/`: `NessieAI/router/posterior_selector.py` imports the generation store only inside `get_active_snapshot` (guard: `NessieAI/tests/router/test_router_import_is_lazy.py`). `NessieAI/router/route_monitoring.py` still imports HiBayes at module scope; nothing on the router's import path imports it.
- Three engine-to-harness imports are frozen, and no new one may be added:
  - `NessieAI/cc/op_registry/paired_evidence.py` imports the bayes harness
  - `NessieAI/build_tools/gen_op_surfaces/route_capabilities.py` imports the corpus and export
  - `NessieAI/hibayes/human_grade_fit.py` imports `bayes_manifest`, lazily
- `NessieAI/tests/api/test_nessie_boundaries.py` enforces both lists for `cc`, `router`, `hibayes`, `ns`, `schema_rag` and `build_tools`; chat_nextseek and the tests are not scanned. It fails on a new back-edge, a new import of `NessieAI.tests`, a listed edge that no longer exists (so both lists stay exact), any `sys.path.insert` of the NessieAI directory, and any bare `e2e` or `pathsetup` import.

## Invariants that span units

- BAML imports stay lazy and guarded: routing degrades to the keyword heuristic, and never stops Django booting.
- `<router_unavailable>` from the BAML router is a failure, not a route. Treating it as one sends every turn to CC.
- Model ids live only in `NessieAI/dmac_assistant/build_context/router_model_class_map.json`. The Bedrock proxy allows only the three ids a CC turn names (the map's `opus`, `opus_fallback` and `sonnet`); any other id, such as Claude Code's own default when a turn has no explicit model id, gets a 403.
- The 8 `.baml` files live only in `NessieAI/dmac_assistant/baml_src/`. The cc-agent image takes them through the Compose named context `dmac_assistant_baml`, so a BAML edit needs both the app and the cc-agent rebuild (guard: `NessieAI/tests/router/test_baml_single_source.py`).
- A judge-schema change touches two files (see `NessieAI/README.md` "To change X, edit Y"; guard: `NessieAI/tests/hibayes/test_judge_models_baml_parity.py`). Never change `PROMPT_VERSION` in `NessieAI/hibayes/judge_human_compare.py`: it is written into judged rows.
- `NessieAI/cc/op_registry/ops.py` is the op registration source of truth; add ops only through `/add-cc-op`. `ops.json` and the plugin surfaces are generated.
- The chat_nextseek context files the cc-agent image bakes (`capabilities.md`, `projects_db.json` and four `min_*.json` catalogs) live only in `NessieAI/chat_nextseek/src/chat_nextseek/context/`. The image takes them through the Compose named context `chat_nextseek`, so an edit there needs both the app and the cc-agent rebuild (list: `CANONICAL_CONTEXT_FILES` in `NessieAI/build_tools/gen_op_surfaces/constants.py`; guard: `NessieAI/tests/cc/test_cc_context_drift_guard.py` for the checkout, and the `cc-agent context` stack-health check in `startup/steps/validate.py`, run after every rebuild, for the built image).
- A chat UI change is two commits: source, then the rebuilt bundle in `static/js/chat_assistant/`.
- `NessieAI/docker/cc-runtime/container/CLAUDE.md` is what the agent is told and ships in the image. Only its marked blocks are generated.
- The live router reads `NessieAI/tests/nessie_tests/corpus.json` for family labels. Do not dockerignore `NessieAI/tests/`.

## Never

- Edit anything under `NessieAI/history/`.
- Create `NessieAI/.env`: `load_dotenv()` in chat_nextseek walks up and would read it before the repo-root `.env`.
- Pass `NessieAI/` wholesale to pytest; name the `NessieAI/tests/<area>` paths.
- Run a paid lane (`RUN_REALSTACK=1`, `--tier full`, `--bayesian`, a proxy Opus probe) without the owner's approval for that run.
- `docker cp nessie_tests nextseek:/app/`. It succeeds and tests stale code. The current form is in `NessieAI/tests/README.md`.

## Box env

`docker/nextseek.env` is rendered once and never re-rendered by `rebuild`. On every box:
- `CATALOG_FILE` must point under `/app/NessieAI/chat_nextseek/`.
- `DMAC_ROUTE_CAPABILITIES_FILE` and `DMAC_ROUTER_MODEL_CLASS_MAP_FILE` must be absent. If present they beat the package default: routing silently drops to the heuristic and every CC turn loses its model id.

## Unit rules

`NessieAI/chat_nextseek/CLAUDE.md`, `NessieAI/dmac_assistant/CLAUDE.md`, `NessieAI/router/CLAUDE.md`,
`NessieAI/cc/CLAUDE.md`, `NessieAI/ns/CLAUDE.md`, `NessieAI/hibayes/CLAUDE.md`, `NessieAI/build_tools/CLAUDE.md`,
`NessieAI/chat_frontend/CLAUDE.md`, `NessieAI/docker/CLAUDE.md`, `NessieAI/tests/nessie_tests/CLAUDE.md`.
