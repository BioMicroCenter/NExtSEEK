# Working in NessieAI/ns/

## Invariants

These are covered by tests. Breaking one is a security or data regression.

- **Only the boolean `True` confirms a write.** The gate compares by identity, and the request model in `nextseek_api/assistant/models_api.py` refuses coercion. Accepting `"true"` or `1` lets an unconfirmed mutation reach the database of record.
- **The gate fires before the agent.** In both the read and write paths of `granular.py` the gate check precedes the agent call. Moving it below turns a refusal into one that already spent tokens and touched the endpoint.
- **This is the only place the read allowlist is enforced on the agent's path.** The sidecar kept only the confirmation check. Softening `write_gate.py` removes the last check, not a duplicate.
- **An unknown op label is denied, and the known-label set is smaller than the dispatch table.** A new handler that calls the gate with its own label is refused until the label is added.
- **`run-ls` refuses any directory outside the cluster runs root**, and shell-quotes its argument. An unvalidated `run_dir` is an arbitrary read of the shared Luria account.
- **A rendered workbook's artifact key is word characters only.** The download route accepts nothing else.
- **`build-upload-xlsx` never writes to NExtSEEK.** It returns a reviewable workbook; a hard-rejected sample type is skipped with its report.
- **The upload workbook emits all four sheets**, even empty ones. A missing sheet makes the parser fall back to the flat format silently.
- **An artifact is served only from inside an artifact root.** `_safe_artifact_path` in `artifacts.py` resolves symlinks and requires `Path.relative_to` containment in `<BASE_DIR>/outputs` or `NEXTSEEK_OUTPUTS_DIR`. A string-prefix check, or a wider root such as `BASE_DIR` or home, lets a stored path read source or secrets.
- **`write_gate.py` and `read_safe_endpoints.json` stay side by side.** The loader resolves the JSON from the module's own directory, and `NessieAI/cc/op_registry/ops.py` reads it at import.

## Landmines

- **The allowlist load sits outside the error handler meant to catch it**, in `nextseek_api/services/assistant.py`, so a missing allowlist escapes as a 500 instead of the documented CONFIG_ERROR envelope.
- **The write gate knows nine labels; the dispatch table has eleven.** `SIDECAR_OPS` in `write_gate.py` holds the seven ported labels plus `graph-schema` and `aggregate`. `run-ls` and `build-upload-xlsx` pass only because they never call the gate (only the `api-read` and `api-write` labels reach it; see `nextseek_api/assistant/CONTRACT.md`).
- **`aggregate` answers with whatever finished at its own 50 s deadline, and its parts keep running.** A part still running then is reported `timed_out` and finishes in the background on its pool thread, as the graph op's work does when the sidecar gives up. Keep `OP_DEADLINE_S` in `aggregate.py` under the sidecar's 60 s (`ns-sidecar/app/ns_client.py`), and keep `aggregate.py` free of anything that builds or widens a scope: `NessieAI/tests/ns/test_aggregate_scope.py` checks its source.
- **Patch the orchestrator at `NessieAI.ns.turn`, not at `nextseek_api.services.assistant`.** The NS pipeline bodies look `run_query`, `run_query_plan` and `run_pipeline_launch` up in `turn.py`. The services module deliberately does not import them, so a stale patch there fails loudly; re-adding the import would make it pass while the real orchestrator runs.
- **Keep `retry.py` free of a module-scope `chat_nextseek` import, and of `NessieAI.ns.turn`.** The evaluator tests stub `load_prompt` before `chat_nextseek.agents` is first imported, and the services module imports `retry.py` at load; `_get_orchestrator` keeps the orchestrator import inside the thread. No test runs `run_retry` itself (the retry tests mock `threading.Thread`), so a change to its body is checked by review only.
- **Nothing compares these wire models with the sidecar's copy** (`NessieAI/docker/ns-sidecar/app/granular_models.py`). Only a digest pin in `NessieAI/tests/cc/test_step7_sidecar_port.py` guards the sidecar file. A model change here drifts the sidecar silently.

## Test command

See `NessieAI/tests/README.md`.

## See also

- `NessieAI/ns/README.md`
- `nextseek_api/assistant/CLAUDE.md`: the consumer, session adapter, models and migration rules that stay with the API.
