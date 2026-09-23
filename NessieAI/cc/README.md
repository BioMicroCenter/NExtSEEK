# `NessieAI/cc/`

## What this is

The Container-CC engine: the sandbox that runs a headless Claude Code agent as an ephemeral
sibling container for every turn the router sends to `container_cc`, and the operation registry
that says which plugin commands that agent may call.

It is a plain package inside `NessieAI/`, not a Django app. The Django shell of the old
`nextseek_api/cc_assistant/` app stays there: `apps.py` (the `cc_assistant` app label, whose
`ready()` arms the LLM cost ledger), the Celery task modules `cc_sweep.py` and `cc_upload_tasks.py`,
and `cc_endpoint_guards.py`. The route decision is in `NessieAI/router/` (`NessieAI/router/README.md`),
and the only HTTP surface is the ViewSet in `nextseek_api/services/cc_assistant.py`, which
hands every routed turn to `start_task` in `NessieAI/cc/turn.py`.

## Surface

**The sandbox.** `run_cc_turn` in `NessieAI/cc/cc_engine.py` drives one turn. `cc_engine.py` is the
largest module here and holds several concerns; read the part you need.

| Concern | Symbol in `NessieAI/cc/cc_engine.py` |
|---|---|
| The agent's whole environment, one constructor | `build_agent_environment` |
| Mounts: one named volume, one subpath per mount | `_build_volumes` |
| Fail-closed check that each subpath directory exists | `_preflight_subpath_dirs` |
| Wall-clock clamp for one turn | `clamp_turn_timeout` |
| Secret-scrub watermark for a stored transcript | `transcript_is_verified_scrubbed` |
| Agent image and network defaults | `DEFAULT_IMAGE`, `DEFAULT_NETWORK` (env `NEXTSEEK_CC_IMAGE`, `NEXTSEEK_CC_NETWORK`) |

| Module | What it does |
|---|---|
| `turn.py` | `start_task`, one routed chat turn on a daemon thread: the route decision, then the NS orchestrator, a CC turn or the out-of-scope reply; plus the helpers that persist the CC session id, the chat_log entry and transcript row, and the memory summaries (`cc_sweep` reuses two of them) |
| `attach.py` | demultiplexes Docker's attach-socket framing (copied from upstream with attribution, because the upstream module pulls in FastAPI) |
| `translate.py` | maps Claude Code `stream-json` onto the progress events the chat panel already renders |
| `cc_artifacts.py` | decides which outputs become a downloadable bundle |
| `cc_transcript_store.py` | zstd-compresses the session `.jsonl` into a `CCSessionTranscript` row |
| `cc_trace.py` | the per-turn activity trace |
| `cc_session.py`, `cc_turn_complete.py` | multi-turn resume and turn-completion persistence (Django-free) |
| `cc_memory.py`, `cc_memory_io.py`, `cc_summary.py` | cross-session memory: which sessions to recall, the mounted files, transcript distillation |
| `cc_turn_context.py`, `ns_turn_context.py`, `ns_digest.py` | the deterministic CC and NS turn-context projections, and the NS digest renderer |
| `prior_turns.py` | stages the chat's previous turns (Search details, rows, downloads, CC answers and files) into the session's `_memory` tree, mounted read-only at `/data/previous_turns`; copies only through the download endpoint's own guard |
| `cc_upload_list.py`, `cc_upload_validate.py` | the upload list and filename validation for agent file uploads |
| `cc_config.py` | `CCPaths` (the external volume and its mount point, read from env) and `CCMemoryConfig` |
| `cc_provision.py` | `build_user_dirs`, the one source of every directory a turn touches, and `resolve_user_project`, which resolves the caller's SEEK project with the caller's own credentials and fails closed |
| `cc_staging.py` | `sweep_user_staging`, which moves sidecar-staged artifacts into the requesting user's own tree |
| `step7_llm_cost_ledger.py` | records real token spend; armed by `nextseek_api/cc_assistant/apps.py` |
| `op_registry/` | the inventory of the plugin commands the agent may call (below) |

**Operation registry.** `NessieAI/cc/op_registry/ops.py` is the registration source of truth, and
`NessieAI/cc/op_registry/export.py` renders it to the committed `ops.json`. The executable shims are
discovered from disk, from the plugin `bin/` directory, by `NessieAI/tests/cc/bin_inventory.py`. Add
an op only through `/add-cc-op` (`.claude/skills/add-cc-op/SKILL.md`).

**Background work.** The idle-session summarizer and the file-upload task are Celery tasks in
`nextseek_api/cc_assistant/cc_sweep.py` and `nextseek_api/cc_assistant/cc_upload_tasks.py`,
registered by `nextseek_api/batch_upload/celery_app.py`.
`nextseek_api/management/commands/cc_sweep_staging.py` is the manual recovery path for staged strays.

## Running and testing

Tests are in `NessieAI/tests/cc/`, together with the step 7 gate tooling (`step7_catalog/`,
`scripts/`, `step7_per_op_evidence.py`, `validate_cc_acceptance.py`). There are three lanes, the
in-container clean lane, the hermetic host lane and the paid real-stack acceptance, and their
commands are in `NessieAI/tests/README.md`. The `host_only` marker (`pyproject.toml`) splits
source-tree checks out of the in-container run.

## Depends on / depended on by

Depends on, outside this directory:

- `NessieAI/dmac_assistant/`: `run_tracker.diff_files`, imported lazily inside `cc_engine.py`.
- `nextseek_api.assistant.models_db`, from `cc_transcript_store.py` and `turn.py`, and `seek.seekdb`,
  lazily, from `cc_provision.py` (both allowed back-edges, `NessieAI/CLAUDE.md` "Boundary").
- `NessieAI/router/` (`policy.py`, the route decision) and `NessieAI/ns/turn.py`, from `turn.py`.
- `NessieAI/ns/read_safe_endpoints.json`, read at import by `op_registry/ops.py` through
  `NessieAI/paths.py`.
- The plugin `bin/` directory under `NessieAI/docker/cc-runtime/`. Emptying it silently empties the
  op inventory.
- `NessieAI/tests/nessie_tests/` and `NessieAI/tests/e2e/`, from `op_registry/paired_evidence.py`:
  one of the three frozen engine-to-harness edges.

Depended on by (non-test):

- `nextseek_api/services/cc_assistant.py`, the ViewSet, which calls `turn.start_task`; that runs
  `run_cc_turn` for a `container_cc` route.
- The Django shell in `nextseek_api/cc_assistant/`, `nextseek_api/management/commands/cc_sweep_staging.py`
  and `nextseek_api/assistant/session_debug.py`.
- `NessieAI/build_tools/gen_op_surfaces/` and `NessieAI/build_tools/plan005_validate_plugins/`, which
  read the registry to generate and validate the plugin surfaces.
- `startup/steps/validate.py`.

See `NessieAI/cc/CLAUDE.md` for the invariants and traps, and `NessieAI/cc/DEPLOY.md` for deploying
and accepting the route.
