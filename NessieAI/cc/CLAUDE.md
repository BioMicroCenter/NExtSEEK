# Working in `NessieAI/cc/`

Routing rules are in `NessieAI/router/CLAUDE.md`; the agent image's rules are in
`NessieAI/docker/CLAUDE.md`.

## Invariants

These hold today and are enforced by tests in `NessieAI/tests/cc/`. Breaking one is a security or
correctness regression, not a refactor.

- **One function builds the agent's environment.** `build_agent_environment` in `cc_engine.py` is the sole constructor; the turn driver and the containment canary both call it, so an inline dict elsewhere cannot smuggle a credential in. The agent carries no AWS credential and none of the shared backend passwords: it reaches the model only through the Bedrock proxy and data only through the authenticated REST API, as the logged-in user, with the one-turn pass Django issues (`NEXTSEEK_TURN_PASS`), never the user's password.
- **Network segmentation is the containment control**, not filesystem permissions. The sibling joins `DEFAULT_NETWORK` (`dmac-cc-net`), declared `internal: true` in the top-level `networks:` block of `docker-compose.yml`, so it has no gateway. `db`, `neo4j`, `seek` and `solr` declare no `networks:` key and stay on the default network only. Adding the agent to the default network hands it L3 reach to services whose password is a committed default.
- **Every mount is a subpath of one named volume.** `_build_volumes` takes each subpath verbatim from the provisioner, never by string-stripping a prefix. Interpolating an unvalidated segment into a subpath is a cross-user read.
- **Directory names are validated before interpolation.** Project, user, session and run identifiers each pass the same segment check in `build_user_dirs` (`cc_provision.py`).
- **The `shared` tree is project-scoped and deliberately carries no user segment** (`shared_subpath` in `build_user_dirs`). That asymmetry is the design, not a bug.
- **Scratch mounted into an agent is per-turn, never the user-scoped root.** The mount takes `run_scratch_subpath`, which exists only when a run id was supplied. Two turns must not see or overwrite each other's files.
- **Spawn fails closed when a backing directory is missing** (`_preflight_subpath_dirs`), because the Docker Engine refuses a subpath mount whose directory does not already exist. <!-- UNVERIFIED: the Engine behaviour is asserted by the guard's own docstring, not confirmed against Docker here -->
- **Server-side clamping beats any client value.** `clamp_turn_timeout` bounds the Debug panel's turn-length control; the UI can never raise the ceiling the deployment set.
- **A transcript is summarized only if it is watermarked as scrubbed.** The Celery beat (`nextseek_api/cc_assistant/cc_sweep.py`) holds no user credential and so cannot scrub; it gates on `transcript_is_verified_scrubbed` and skips anything unverified. A session that keeps warning every beat is the intended operator signal.
- **A transcript scrub takes its secrets from the login Django holds for the turn** (`scrub_secrets`: the user's password and the turn pass), never from the agent's container env, which holds no password since the turn pass. A scrub given no password writes no clean watermark. Transcripts keep `DMAC_PATH_MAPPINGS` (user-facing paths); log lines mask it.
- **Django never follows a link or trusts a path in a folder an agent or the sidecar can write.** The chat's `cc-state`, the turn's `scratch` and the user's `_staging` are written by code Django does not trust, so every read, write, rename, mode change, listing or deletion Django makes there goes through `NessieAI/cc/safe_fs.py` (or a folder fd from `safe_fs.open_dir` with a no-follow call), and a turn publishes only after its container has exited. Every `safe_fs` call passes a trusted root, a mount's backing root (`cc-state/<session>`, `scratch/<run_id>`, `_staging`, registered by `build_user_dirs` and `staging_root_for`) or a Django folder above one, with every path component an agent or the sidecar controls in `rel`; `safe_fs` refuses a root below a registered one. `NessieAI/tests/cc/test_cc_agent_folders_every_folder.py` keeps planting links in all three folders and guards the functions that work there against path-based file calls; it and the other test_cc_agent_folders modules are blocking in CI (`ci/blocking_lanes.py`).
- **The turn's own folder `{project}/{user}/_turn/<run_id>` is Django's.** It holds the turn's `vocabulary.json`, is mounted read-only at `/data/turn`, is written through `safe_fs` with mode set on the open folder, is never covered by an agent mount, and is removed once the container has stopped.
- **Only the staging sweep writes into a user's own subtree.** The sidecar's volume mount is pinned to the reserved staging subpath (the `nextseek-sidecar` service in `docker-compose.yml`), so it cannot reach `{project}/{user}/`, and `sweep_user_staging` derives its destination only from the validated request identity, never from a staged file's name.
- **`op_registry/__init__.py` stays stdlib-only.** Attribute access goes through its lazy `__getattr__`, so importing the package does not pull in pydantic.
- **`ops.py` is the registration source of truth**; `ops.json` is generated by `op_registry/export.py`. Never hand-edit the JSON, and add ops only through `/add-cc-op`.

## Landmines

- **The step 7 gate catalog is read at import time, not at test time.** `NessieAI/tests/cc/step7_gate_catalog.py` binds `step7_catalog/` and scans the plugin `bin/` directory at module scope. Moving or emptying either turns a passing suite into import errors.
- **`op_registry/ops.py` reads `NessieAI/ns/read_safe_endpoints.json` at import.** If the file moves without `NessieAI/paths.py` following it, the whole registry stops importing.
- **Some tests load archived scripts from `NessieAI/history/cc/` by path** (`NessieAI/tests/cc/test_cc_scripts_attribution.py`, `NessieAI/tests/cc/test_task12_remaining_holes.py`). They are `host_only`, and they skip where `NessieAI/history/` is absent, as it is in the app image (`.dockerignore`). The scripts predate the move, so the tests answer their old module names and repo-root layout through `PRE_MOVE_MODULES` and `PRE_MOVE_DIRS` in the attribution test. A change those tests need goes there or into a live copy, never into the frozen history tree.
- **A hand-rolled op catalog passes its own tests and then fails the generated-surface check.** Follow `.claude/skills/add-cc-op/SKILL.md`.
- **`NessieAI/tests/cc/test_cc_realstack.py` spends real money** when `RUN_REALSTACK=1` is set. Never set it without the owner's approval for that run.

## Test command

See `NessieAI/tests/README.md` ("CC clean lane", "CC hermetic lane").

## See also

- `NessieAI/cc/README.md`: what each module does, and the dependency map.
- `NessieAI/cc/DEPLOY.md`: deploying and accepting the Container-CC route.
- `NessieAI/docker/CLAUDE.md`: the agent image, the proxy and the sidecar.
- `NessieAI/router/CLAUDE.md`: how a turn gets routed here.
- `DEPLOYMENT.md` §9: the security invariants of the Container-CC route.
