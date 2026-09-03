# Working in `nextseek_api/assistant/`

## Invariants

These hold today and are covered by tests. Breaking one is a security or data
regression, not a refactor.

- **Only the boolean `True` confirms a write.** The gate compares by identity at
  `nextseek_api/assistant/write_gate.py:86` and the request model refuses
  coercion at `nextseek_api/assistant/models_api.py:279`. Relaxing either lets the
  string "true" or the integer 1 arrive from a shell or a JSON body and execute an
  unconfirmed mutation against the database of record.
- **The gate fires before the agent, not after it.** In the write path the check
  at `nextseek_api/assistant/granular.py:113` precedes the plan build at
  `nextseek_api/assistant/granular.py:114`, and in the read path the check at
  `nextseek_api/assistant/granular.py:102` precedes the outbound call at
  `nextseek_api/assistant/granular.py:103`. Moving either below its agent turns a
  refusal into a refusal that already spent model tokens and already touched the
  endpoint.
- **This package is the only place the read allowlist is enforced on the agent's
  path.** The sidecar deliberately kept just the confirmation check and retired
  its own allowlist to NExtSEEK — `docker/ns-sidecar/app/write_gate.py:14-15`, its
  module docstring saying so at `docker/ns-sidecar/app/write_gate.py:1-3`. Softening
  `nextseek_api/assistant/write_gate.py:92` removes the last check, not a duplicate
  one.
- **An unrecognised op label is denied, and the known-label set is smaller than
  the dispatch table.** The deny is at
  `nextseek_api/assistant/write_gate.py:99-100`, the seven ported labels at
  `nextseek_api/assistant/write_gate.py:29-31`, and nine handlers at
  `nextseek_api/assistant/granular.py:261-271`. A new handler that calls the gate
  with its own label is refused with WRITE_BLOCKED until that label is added.
- **`run-ls` refuses any directory outside the cluster runs root.** The
  containment test is `nextseek_api/assistant/granular.py:204-207` and the
  argument is shell-quoted at `nextseek_api/assistant/granular.py:210`.
  Interpolating an unvalidated `run_dir` is arbitrary read of the shared Luria
  account under the service key.
- **A task id is not a capability token.** The consumer demands an authenticated
  user and then ownership, at
  `nextseek_api/assistant/consumers.py:103-120`, and refuses all three failure
  cases identically. Dropping either half turns a leaked UUID into another user's
  live progress stream; the Origin check at
  `nextseek_api/assistant/consumers.py:46-60` is defence in depth behind that, not
  instead of it.
- **Bundle history is merged under a row lock, never assigned over.**
  `nextseek_api/assistant/session_adapter.py:89-118` re-reads the locked row and
  folds by bundle id for the reason recorded at
  `nextseek_api/assistant/session_adapter.py:58-72`. Replacing the merge with a
  plain write deletes a concurrent turn's bundle, and the visible symptom is a
  follow-up question answered against the wrong result set.
- **Models here belong to the parent app's label and migration chain.** Each class
  declares it explicitly, as at `nextseek_api/assistant/models_db.py:23`, and the
  tables are created by migrations such as
  `nextseek_api/migrations/0002_querytask.py:32`. Adding a model without the label,
  or giving this package a migrations directory of its own, splits the chain and
  the next deploy stops on an unapplied dependency.
- **A rendered workbook's artifact key must be word characters only.** The
  sanitiser at `nextseek_api/assistant/granular.py:251-255` exists because the
  download route accepts nothing else; a key that keeps its dot produces a bundle
  whose URL 404s while the file sits on disk.
- **`build-upload-xlsx` never writes to NExtSEEK.** A hard-rejected sample type is
  skipped with its report returned, per
  `nextseek_api/assistant/granular.py:214-221`. Making it upload turns a reviewable
  artifact into an unreviewed mutation of the sample database.
- **The upload workbook emits all four sheets.** A missing sheet makes the
  batch-upload parser fall back to the flat format silently, which is why
  `nextseek_api/assistant/upload_workbook.py:23-27` emits every one even when
  empty.

## Landmines

- **Five of this package's six current test failures are one drift in committed
  state.** The canonical capabilities document and the copy baked into the agent
  image are not byte-identical: they diverge at line 35, at
  `chat_nextseek/src/chat_nextseek/context/capabilities.md:35` and
  `docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md:35`.
  The equality is asserted directly at
  `nextseek_api/assistant/tests/test_route_capabilities.py:284`, and the builder
  raises on it at `build_tools/gen_op_surfaces/route_capabilities.py:231-232`, so
  four more tests die inside the payload builder. Measured 2026-09-03. Nothing in
  this package caused it and nothing in this package can fix it.
- **The sixth failure is the lane, not the code.** That test shells out to `git
  show` against a pinned commit at
  `nextseek_api/assistant/tests/test_route_capabilities.py:338-347`, so it fails
  wherever the checkout's git directory is not reachable from inside the container.
- **The largest module under `tests/` tests nothing in this package.** Its
  module-scope imports at
  `nextseek_api/assistant/tests/test_route_capabilities.py:16-44` come from
  `build_tools`, `dmac_assistant`, `nessie_tests` and the Container-CC op registry,
  and it is pinned as a named CI lane by path at `build_tools/plan005_gate.py:28`.
  Running this directory's tests therefore fails on other subsystems' state, and
  deleting the module quietly removes a CI lane.
- **The allowlist load sits outside the error handler that is supposed to catch
  it.** `nextseek_api/services/assistant.py:1261` calls it while the try block only
  opens at `nextseek_api/services/assistant.py:1268`, so the
  `AllowlistMissingError` documented as mapping to CONFIG_ERROR at
  `nextseek_api/assistant/write_gate.py:42-43` escapes as an unhandled 500 instead
  of the canonical envelope. That mapping exists nowhere in code: grepping the
  literal CONFIG_ERROR over every `.py` file under `nextseek_api/` returns only two
  docstrings and three lines of test commentary, and no call that emits it.
- **`CONTRACT.md` in this directory has drifted from the code beside it in three
  ways.** Its op table lists seven ops while the dispatch table at
  `nextseek_api/assistant/granular.py:261-271` holds nine; its test counts predate
  the 302 tests the suite now collects; and its claim that the SQLite test settings
  cannot build the database on this branch did not reproduce on 2026-09-03 — the
  full run below used `dmac.test_settings` with migrations enabled. Treat its op
  semantics as authoritative and its numbers as history.
- **Nothing compares this package's wire models with the sidecar's copy of them.**
  The only guard is a sha256 literal pinning the sidecar file to its own port
  commit, at `nextseek_api/cc_assistant/tests/test_step7_sidecar_port.py:91`;
  searching every `.py` file in the repo for `granular_models` returns that pin and
  one neighbouring list entry, and no comparison against
  `nextseek_api/assistant/models_api.py`. Editing a model here drifts the sidecar
  silently and the break shows up as a runtime validation error in another
  container.
- **A deployment can turn the WebSocket off without touching this code.** The
  entrypoint selects the server at `docker/scripts/entrypoint.sh:61-65`, and the
  WSGI branch serves no WebSocket at all, which the frontend absorbs by polling.
  A report that "the progress socket is broken" is that setting until proven
  otherwise.
- **There is no `conftest.py` anywhere in this package**: a find for a file of that
  name beneath `nextseek_api/assistant` returns nothing, and the only conftest in
  scope is the parent's, `nextseek_api/conftest.py:1-83`. Combined with the real settings
  module named at `pyproject.toml:147`, a bare `pytest` aimed here loads production
  settings and fails on a missing chat config rather than on anything real.
- **`session_adapter.save` swallows every failure of its locked path.** The bare
  handler at `nextseek_api/assistant/session_adapter.py:119-122` falls through to an
  unlocked write, so on a backend without row locking the merge protection is gone
  and nothing is logged. A lost bundle looks like a model mistake, not a race.
- **`models_db.py` is the evaluation subsystem's table module as well as the
  chat's.** Nine of its thirteen classes are `eval_*` tables, one being the spend
  reservation at `nextseek_api/assistant/models_db.py:240-265`, and seven module-scope
  imports under `nextseek_api/eval/` reach into this module, one of them taking that
  reservation class and the approved-run manifest together
  (`nextseek_api/eval/run_authorization.py:14`). Trimming a "chat" model file breaks
  the paid-run authorization store.
- **`descriptions_cc.py` is imported by nothing.** Grepping every `.py` file in the
  repo for its module name and for each of its three constants returns only the
  file itself plus the conventions validator and its test, which read it by path
  at `scripts/validate_viewset_conventions.py:22-26`. Deleting it as dead code
  fails that validator rather than any import.
- **Every progress event rewrites the whole JSON column.**
  `nextseek_api/assistant/pipeline_adapter.py:30-44` reads, appends and saves the
  full `progress` list per event, so a chatty turn is O(events squared) in written
  bytes and a slow turn is visible as row contention rather than as agent latency.
- **A running instance is not this branch.** The runbook's first deployment rule
  is that only committed code from the shared integration branch is deployed, and
  that patching a container makes the running system diverge from git
  (`DEPLOYMENT.md:80-84`). Behaviour seen on a deployed host is evidence about the
  image that host last built, so importing it as evidence here sends you debugging
  a difference that does not exist in the code in front of you.

## Test command

Run the free lane against a writable copy of the checkout, because the settings
module makes two directories at import time and the local-settings overlay has to
live inside the tree:

```
cp -a <checkout> /tmp/assistant-lane && cd /tmp/assistant-lane
mkdir -p schema_rag/duckdb schema_rag/embedding_models
cp startup/dev/lane_local_settings.py dmac/local_settings.py
docker run --rm --network none \
  -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONDONTWRITEBYTECODE=1 -e GCP_API_KEY=dummy \
  -e CATALOG_FILE=/src/chat_nextseek/agent_model_catalog.json \
  -v "$PWD":/src -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest nextseek_api/assistant/tests -q -p no:cacheprovider
```

Last run here, 2026-09-03: 288 passed and 6 failed, every failure inside one
module, plus 8 tests that skipped themselves and 14 subtests that passed. Do not
run this against the live `nextseek` container: its `/app` is baked from a
different checkout and another session may be using the stack.

## See also

- See `nextseek_api/assistant/README.md` for what each module does, why the
  overlay is needed, and the dependency map in both directions.
- See `nextseek_api/assistant/CONTRACT.md` for the endpoint contract, subject to
  the drift noted above.
- See `nextseek_api/cc_assistant/CLAUDE.md` for the routing and sandbox traps of
  the subsystem that consumes these models.
- See `ci/gate/live_routes.py:16-27` for the throwaway-container recipe this lane
  is a variant of.
- See the repo-root `CLAUDE.md` for the stack, the rebuild commands and the
  pytest configuration caveats.
