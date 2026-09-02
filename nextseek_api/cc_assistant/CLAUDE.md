# Working in `nextseek_api/cc_assistant/`

## Invariants

These hold today and are enforced by tests. Breaking one is a security or
correctness regression, not a refactor.

- **One function builds the agent's environment.** `nextseek_api/cc_assistant/cc_engine.py:282`
  is the sole constructor, and both the turn driver and the containment canary
  call it, so an inline dict elsewhere cannot smuggle a credential in. The agent
  carries zero AWS credentials and none of the shared backend passwords; it
  reaches the model only through the auth proxy and reaches data only through
  the authenticated REST API as the logged-in user.
- **Network segmentation is the containment control**, not filesystem
  permissions. The sibling joins the dedicated network named at
  `nextseek_api/cc_assistant/cc_engine.py:59`, which by construction excludes
  Neo4j, MySQL, SEEK and Solr. Putting the agent on the default compose network
  would hand it L3 reach to services whose password is a committed default.
- **Every mount is a subpath of one named volume.** `nextseek_api/cc_assistant/cc_engine.py:932`
  builds them and takes each subpath value verbatim from the provisioner —
  never by string-stripping a prefix. Interpolating an unvalidated segment into
  a subpath is a cross-user read.
- **Directory names are validated before interpolation.** Project, user,
  session and run identifiers all pass the same segment regex in
  `nextseek_api/cc_assistant/cc_provision.py:99`.
- **The `shared` tree is project-scoped and deliberately carries no user
  segment** — `nextseek_api/cc_assistant/cc_provision.py:130`. That asymmetry is
  the design, not a bug.
- **Scratch mounted into an agent is per-turn, never the user-scoped root.**
  Two turns must not be able to see, overwrite, or act on each other's files.
- **Spawn fails closed when a backing directory is missing**
  (`nextseek_api/cc_assistant/cc_engine.py:988`), because the Docker Engine
  refuses a subpath mount whose directory does not already exist.
- **Server-side clamping beats any client value.** The Debug panel's turn-length
  control is bounded at `nextseek_api/cc_assistant/cc_engine.py:103`; the UI can
  never raise the ceiling the deployment set.
- **A transcript is summarized only if it is watermarked as scrubbed.** The
  Celery beat holds no user credential and so cannot scrub, so it gates on
  `nextseek_api/cc_assistant/cc_engine.py:527` and skips anything unverified.
  A session that keeps warning every beat is the intended operator signal.
- **Only the staging sweep writes into another subtree.** The sidecar's own
  mount cannot reach `{project}/{user}/`; the destination is derived from the
  current request's validated identity, never from a staged file's name.
- **BAML imports stay lazy and guarded** — `nextseek_api/cc_assistant/router.py:135`.
  A vendoring or dependency hiccup must degrade routing, never stop Django from
  booting.
- **`op_registry/__init__.py` must stay stdlib-only.** Attribute access is
  routed through the lazy `__getattr__` at
  `nextseek_api/cc_assistant/op_registry/__init__.py:61` precisely so importing
  the package does not drag in pydantic.
- **`ops.py` is the registration source of truth**; `ops.json` is generated
  output written by `nextseek_api/cc_assistant/op_registry/export.py:14`. Never
  hand-edit the JSON.

## Landmines

- **`.vetting/` is not documentation.** 65 files of superseded automated
  review-iteration logs from an earlier build. Do not read them for current
  behaviour and do not cite them.
- **The 10 `SPEC-*.md` / `PLAN-*.md` files here are superseded** and are
  scheduled to move to an archive. `PLAN-3-ui-based-io.md` alone is 155 KB.
  They describe intent at the time of writing, not the tree.
- **`LIVE_EVIDENCE.md` and `DEPLOY.md` are stale and under separate triage.**
  Their claims are not current. `nextseek_api/cc_assistant/LIVE_EVIDENCE.md:8`
  documents a test-runner limitation on one box at one moment, which readers
  keep mistaking for a standing constraint.
- **`acceptance_evidence/` is read at import time, not at test time.**
  `nextseek_api/cc_assistant/step7_gate_catalog.py:18` binds the catalog
  directory, and `nextseek_api/cc_assistant/step7_gate_catalog.py:25` scans the
  plugin bin directory — both at module scope. Moving or emptying either
  directory turns a passing suite into import errors.
- **`evidence/` is likewise load-bearing.** A test loads a probe script out of
  it by relative path at `nextseek_api/cc_assistant/tests/test_cc_scripts_attribution.py:430`.
- **`op_registry/ops.py` reads a JSON file from a sibling app at import**
  (`nextseek_api/cc_assistant/op_registry/ops.py:19`). If
  `nextseek_api/assistant/read_safe_endpoints.json` moves, the whole registry
  stops importing.
- **The hermetic lane in the deployment runbook is broken as printed.** Its
  dependency list omits Django, so 62 modules error during collection before
  any test runs. Measured on 2026-09-02; the fix is to add Django to the
  `--with` list or pass `--continue-on-collection-errors`.
- **There is no `conftest.py` anywhere under `tests/`.** The default settings
  module in `pyproject.toml:147` is the real one, not the test one, so a bare
  root-level `pytest` invocation over this directory behaves differently from
  the documented lanes.
- **`cc_engine.py` is 1,908 lines and holds several distinct concerns.** Read
  the section you need; do not read it top to bottom expecting a narrative.
- **`router.py` opens with a `try/except ImportError` dual import**
  (`nextseek_api/cc_assistant/router.py:16`) so the module also loads outside
  the package. Adding a plain relative import to the top of that file breaks
  the standalone path silently.
- **Adding an operation is a skill, not an edit.** Follow
  `.claude/skills/add-cc-op/SKILL.md`; a hand-rolled parallel catalog will pass
  its own tests and fail the generated-surface gate.
- **`nextseek_api/cc_assistant/tests/test_cc_realstack.py:55` spends real
  money** when its env flag is set. Do not set it casually.

## Test command

The canonical behavioural suite, run inside the live container so it has the
database grant and the secrets:

```
docker exec -w /app nextseek uv run --no-sync python -m pytest \
  nextseek_api/cc_assistant/tests/ --create-db -k 'not realstack' \
  --ignore=nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py \
  --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py
```

(not run) — it needs a running stack. Never widen it to the whole
`nextseek_api/` tree in-container; that path carries hundreds of known
environmental harness errors that are not regressions.

## See also

- See `nextseek_api/cc_assistant/README.md` for what each module does, the
  dependency map, and the three test lanes with the one measured result.
- See `DEPLOYMENT.md:483` for the lane table and the full deployment runbook.
- See `nextseek_api/cc_assistant/DEPLOY.md:14` for subsystem-specific deploy
  notes — pending review, treat as unverified.
- See `docker/cc-runtime/Dockerfile:51` for how the agent image and its plugin
  bin directory are assembled — and note the build-time guard at
  `docker/cc-runtime/Dockerfile:60` that refuses to ship an empty catalog.
- See the repo-root `CLAUDE.md` for the router overrides, sticky-CC rule, and
  where the ViewSet lives.
