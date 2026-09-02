# `nextseek_api/cc_assistant/`

## What this is

The Container-CC subsystem: the per-turn **route decision** for the chat
assistant, and the **sandbox** that runs a headless Claude Code agent as an
ephemeral sibling container when that decision is `container_cc`.

It is a real Django app — installed as `nextseek_api.cc_assistant.apps.CcAssistantConfig`
at `dmac/settings.py:179` — but an unusual one. It declares **no models and no
migrations of its own**; the ORM row it writes is created at
`nextseek_api/cc_assistant/turn_ledger.py:28`, from a model class defined in
`nextseek_api/assistant/models_db.py`. Its `AppConfig.ready()` hook exists solely
to arm the LLM cost ledger (`nextseek_api/cc_assistant/apps.py:9`). It registers
**no URLs**: the single HTTP surface is a ViewSet registered from outside the
boundary at `nextseek_api/urls.py:40`.

So the package is a library of engine parts, not a request handler. Two thirds
of it is test code — 202 Python files, 143 of them under `tests/`.

Three route constants define the whole decision space —
`nextseek_api/cc_assistant/router.py:30-32` — `nextseek_query`,
`container_cc`, `unrelated`.

## Surface

**Routing.** `decide()` at `nextseek_api/cc_assistant/router.py:308` is the one
public entry point. Beneath it sit four strategies in precedence order. A
comparative-posterior selector goes first when its Django feature flag is on
(`nextseek_api/cc_assistant/posterior_selector.py:38`); a returned selection
short-circuits the rest at `nextseek_api/cc_assistant/router.py:285`, so the
BAML router is never consulted. Otherwise a BAML classifier assigns a task
family, a BAML router picks a destination, and a keyword regex
(`nextseek_api/cc_assistant/router.py:107`) is the last resort when BAML is
unreachable. Every `dmac_assistant` import is deferred into
`nextseek_api/cc_assistant/router.py:135`. Two telemetry-only overlays observe
the outcome without changing it: `nextseek_api/cc_assistant/risk_overlay.py:1`
and `nextseek_api/cc_assistant/route_monitoring.py:1`. The classifier's label
space is owned by the `nessie_tests` corpus, not by this package
(`nextseek_api/cc_assistant/family_labels.py:21`).

**The sandbox.** `run_cc_turn()` at `nextseek_api/cc_assistant/cc_engine.py:1005`
is the turn driver, inside the largest file here
(`nextseek_api/cc_assistant/cc_engine.py:1908` is its last line). Its pieces:

| Concern | Where |
|---|---|
| Complete agent env, single source of truth | `nextseek_api/cc_assistant/cc_engine.py:282` |
| Mount payloads, one named volume, per-mount subpath | `nextseek_api/cc_assistant/cc_engine.py:932` |
| Fail-closed check that each subpath dir exists | `nextseek_api/cc_assistant/cc_engine.py:988` |
| Wall-clock clamp for a single turn | `nextseek_api/cc_assistant/cc_engine.py:103` |
| Secret-scrub watermark for a stored transcript | `nextseek_api/cc_assistant/cc_engine.py:527` |
| Agent image and dedicated network defaults | `nextseek_api/cc_assistant/cc_engine.py:49` |

Around it: `nextseek_api/cc_assistant/attach.py:3` demultiplexes Docker's
stdcopy framing (copied verbatim from upstream with attribution, because the
upstream module drags in FastAPI); `nextseek_api/cc_assistant/translate.py:1-7`
maps Claude Code `stream-json` onto the six progress events the existing React
frontend already renders; `nextseek_api/cc_assistant/cc_artifacts.py:3` decides
what becomes a downloadable bundle; `nextseek_api/cc_assistant/cc_transcript_store.py:3`
zstd-compresses the session `.jsonl` into a DB row.

**Path layout.** `nextseek_api/cc_assistant/cc_config.py:23` names the external
volume and its mount point, read from env at `nextseek_api/cc_assistant/cc_config.py:36`;
`nextseek_api/cc_assistant/cc_config.py:54` holds the cross-session-memory knobs.
`nextseek_api/cc_assistant/cc_provision.py:99` is the single source of truth for
every directory a turn touches, and `nextseek_api/cc_assistant/cc_provision.py:156`
resolves the caller's SEEK project with the caller's own credentials, failing
closed rather than guessing.

**Background work.** Two Celery tasks: an idle-session summarizer at
`nextseek_api/cc_assistant/cc_sweep.py:91` and a file-upload task at
`nextseek_api/cc_assistant/cc_upload_tasks.py:29`. A third sweep,
`nextseek_api/cc_assistant/cc_staging.py:253`, moves sidecar-staged artifacts
into the requesting user's own tree.

**Operation registry.** `op_registry/` is the authoritative inventory of the
plugin commands the agent may call. `nextseek_api/cc_assistant/op_registry/export.py:14`
renders it to the committed `ops.json`; `nextseek_api/cc_assistant/bin_inventory.py:38`
discovers the executable shims from disk rather than a hardcoded list, rooted at
`nextseek_api/cc_assistant/bin_inventory.py:19` (20 `nextseek-*` shims today).

**Evidence and gates.** `nextseek_api/cc_assistant/step7_llm_cost_ledger.py:3`
records real token spend; `nextseek_api/cc_assistant/step7_per_op_evidence.py:3`
holds the pure evidence logic for forced-CC per-op proofs. Paid orchestration
lives in `nextseek_api/cc_assistant/scripts/full_ui_e2e.py:2` and
`nextseek_api/cc_assistant/scripts/verify_prod_readiness_manifest.py:3`.

Three directories here are **load-bearing inputs the package reads**, not
scratch it writes. `acceptance_evidence/` supplies the gate exercise catalog
and the instance binding (`nextseek_api/cc_assistant/step7_gate_catalog.py:19`
and `nextseek_api/cc_assistant/step7_gate_catalog.py:20`).
`tests/acceptance_evidence/` is the home for generated run bundles, and its
validator refuses any bundle whose files are only Markdown
(`nextseek_api/cc_assistant/tests/acceptance_evidence/step7/README.md:13`).
`evidence/` holds a live probe script
(`nextseek_api/cc_assistant/evidence/run_1c_claude_md_live_probe.py:2`).

## Running and testing

Three lanes, not interchangeable.

1. **Hermetic host lane.** No database, no Docker, no spend. I ran this one on
   2026-09-02 and confirmed the result: 908 passed, 22 failed, 5 skipped, 62
   collection errors, in 5.6 seconds. Both the failures and the errors are
   environmental — modules needing `docker`, `chat_nextseek`, or a configured
   Django, under a dependency set that supplies none of them.
2. **In-container clean lane** — the canonical behavioural suite, needing the
   live container's secrets, DB grant and network. (not run)
3. **Paid real-stack acceptance**, gated behind an env flag at
   `nextseek_api/cc_assistant/tests/test_cc_realstack.py:55`, which spends real
   money on Opus turns through the auth proxy. (not run)

The `host_only` marker declared at `pyproject.toml:148` splits source-tree
hygiene tests out of the in-container run; 9 test modules here use it.

## Depends on / depended on by

Depends on, outside this directory:

- The vendored BAML router in `dmac_assistant/`, imported only inside the
  deferred loader at `nextseek_api/cc_assistant/router.py:135`.
- `nextseek_api/assistant/models_db.py` for the ORM model this package writes — `nextseek_api/cc_assistant/turn_ledger.py:28`.
- `nextseek_api/eval/` for the routing generation store, consulted at
  `nextseek_api/cc_assistant/posterior_selector.py:47`.
- `nessie_tests/corpus.json` for the classifier label space, resolved at
  `nextseek_api/cc_assistant/family_labels.py:21`.
- `seek.seekdb`, used host-side only to resolve a project, behind the factory at
  `nextseek_api/cc_assistant/cc_provision.py:150`.
- `docker/cc-runtime/build_context/plugins/nextseek/bin/`, the shim directory
  the registry scans — `nextseek_api/cc_assistant/bin_inventory.py:22`. Deleting
  it silently empties the op inventory.
- `nextseek_api/assistant/read_safe_endpoints.json`, read eagerly at
  `nextseek_api/cc_assistant/op_registry/ops.py:19`.

Depended on by:

- `nextseek_api/services/cc_assistant.py:351` — the only production caller.
  Route overrides (`force_route`, the pipeline wizard gate, sticky CC) are
  applied there in the precedence documented at
  `nextseek_api/services/cc_assistant.py:354`, not here.
- `build_tools/gen_op_surfaces/`, which regenerates the agent's skills, commands
  and route capabilities from this registry —
  `build_tools/gen_op_surfaces/route_capabilities.py:38`.
- `build_tools/plan005_gate.py:26`, which pins a test module here as a named CI
  lane.
- The Celery beat schedule, which fires the summary sweep every 300 seconds —
  `nextseek_api/batch_upload/celery_app.py:42`.

See `nextseek_api/cc_assistant/CLAUDE.md` for the invariants and traps.
