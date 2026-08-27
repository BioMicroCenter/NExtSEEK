# CC / dmac_assistant testing — comprehensive review

Scope: the Container-CC ("CC" mode) side of the NExtSEEK assistant. This maps
the three code pieces, inventories every CC test/harness, and answers the six
questions posed for the unified-router-harness design discussion.

Repo root: `/home/cdemu/code/dmac/docker/dev-v3-merge` (branch `dev-v3-merge`).
All paths below are relative to that root unless absolute. READ-ONLY review — no
code was modified.

---

## 0. The three pieces and how they relate

| Piece | What it is | Where its tests live |
|---|---|---|
| **`dmac_assistant/`** | Vendored subset of the upstream `dmac-assistant` runtime library. NExtSEEK uses only two things from it: `dmac_assistant.router.*` (the BAML `RouteQuery` LLM router that picks NS-vs-CC) and `dmac_assistant.streamjson` (the Claude `stream-json` parser). The FastAPI/uvicorn/ws bridge, auth, Dropbox model, and offline HiBayes `eval/` are **intentionally not vendored**. | **None in-package.** `dmac_assistant/` ships **zero** `test_*.py` / `tests/` / pytest config (verified: `find dmac_assistant -name 'test_*.py' -o -type d -name tests` → empty; `pyproject.toml` has no `[tool.pytest]`). Its behavior is tested indirectly through the Django app's router tests. |
| **`nextseek_api/cc_assistant/`** | The **Django integration** ("the bridge"). `router.py` wraps dmac's `RouterAgent` + a keyword heuristic fallback; `cc_engine.py` spawns the ephemeral per-turn agent container; `cc_memory` / `cc_turn_context` / `ns_turn_context` / `ns_digest` / `router_context` build the cross-mode memory + router history; plus **all the CC test files and the step7 gate harnesses**. | `nextseek_api/cc_assistant/tests/` (~70 files) + `scripts/` harnesses. This is where "dmac_assistant testing" overwhelmingly lives. |
| **`docker/cc-runtime/`** | The **container image** (built as `dmac-assistant:poc`, run per-turn as `dmac-cc-agent-<uuid>`). Ships the `nextseek` plugin: the `nextseek-*` bin **ops** (shims the agent calls), `SKILL.md`/`container/CLAUDE.md` (the in-container agent instructions), reference catalogs, and `tools/e2e/judge_runner.py` (an in-image BAML judge). | `docker/cc-runtime/tests/unit/test_batch_upload_*.py` (hermetic unit tests for the batch-upload payload builder) + `build_context/plugins/nextseek/bin/tests/test_dispatch_pipeline.py`. |

**So "dmac_assistant testing" = three layers that are never exercised by one
harness.** The library (`dmac_assistant`) is tested only via the bridge's router
tests; the bridge (`cc_assistant`) has a large hermetic unit suite plus three
paid live harnesses; the image (`cc-runtime`) has its own hermetic unit suite
for the shims and an in-image judge with **no vendored host-side driver**.

Production request flow (for orientation):
`services/cc_assistant.py::_start_task` → `_decide_route` (`cc_assistant.py:203`)
→ emits `route_decided` event (`cc_assistant.py:333`) → NS branch runs
`run_query` in-process, or CC branch runs `cc_engine.run_cc_turn` which spawns
the `cc-runtime` image and the agent calls `nextseek-*` ops.

---

## 1. Inventory & architecture — every CC harness/test

### 1a. `scripts/step7_gate3d_per_op.py` — per-op forced-CC live harness (PAID)

- **What it is** — 8 fresh-session **forced-CC** turns, one per bin op, proving
  the flow *user query → CC route → op → answer* (`step7_gate3d_per_op.py:2-9`).
  Explicitly **replaces** the old direct-exec matrix which "never routed through
  CC" (`:8-9`).
- **Entry point / how it runs** — a `__main__` script run **inside the live
  `nextseek` container** via `docker exec`, after the sidecar deploy, proxy
  alias healthy, and `STEP7_LLM_LEDGER=1` (`:12-19`). Each op calls the
  production `cc_engine.run_cc_turn(... session_id=None ...)` on its own thread
  with a fresh `cc_state_key == cc_run_id` (`:110-133`) — i.e. a genuine CC turn
  through the ephemeral container.
- **What it needs** — the running stack (nextseek + sidecar + `dmac-bedrock-proxy`
  + `dmac-cc-net`), SEEK creds (`SEEK_TEST_USER/PASS`), a real **Anthropic/Bedrock
  budget**, and a bundle dir. Budget-capped: `PER_TURN_BUDGET`
  (`NEXTSEEK_CC_MAX_BUDGET_USD`, default $0.50) and a pre-emptive
  `TOTAL_BUDGET_CAP` ($10) that aborts before an op if cumulative spend would
  exceed it (`:49-54`, `:216-220`).
- **The input set** — `OP_QUERIES: dict[str,str]` (`:63-84`), 8 **user-approved,
  verbatim-from-`catalog.json`** natural-language questions, one per op, e.g.
  `"nextseek-entity-extract": "What monkeys exist in the database?"`,
  `"nextseek-graph": "Show me all NHPs in the Impact project."`,
  `"nextseek-report": "How many samples were uploaded for IMPACT from 2023 to 2025?"`.
- **What it asserts** — per-op, via `step7_per_op_evidence.evaluate_op_row`
  (`step7_per_op_evidence.py:242-310`): `cost_usd > 0` (every CC turn costs a
  Bedrock turn — fail-closed), `not is_error`, non-empty answer, no
  backend-error phrase (`_BACKEND_ERROR_PATTERNS`), and the op's tool_result
  `status == "ok"`. Crucially it does **not** assert answer **content/correctness**:
  if the *expected* op was not invoked it records a soft `needs_review` flag, not
  a failure (`:272-277`, "LLM routing, not a code bug"). It also asserts
  fresh-session isolation across the 8 (`assert_fresh_sessions`, `:313-332`).
- **Paid/slow** — yes: 8 real Opus turns, 180s hard cap each; has cost-recovery
  logic that reconstructs spend from transcript `usage` when a turn is killed at
  the cap before the result frame (`step7_per_op_evidence.py:155-188`).

### 1b. `scripts/full_ui_e2e.py` — approval-driven full-UI paid E2E (PAID)

- **What it is** — the only harness that drives the **real browser through the
  actual chat UI over HTTP**. Reads a frozen `approval.json` and, for each
  question, calls `run_variant_browser` (the real Playwright runner in
  `chat_nextseek/e2e/playwright/runner.py`, path-pinned at `full_ui_e2e.py:138`).
- **Entry point** — `python full_ui_e2e.py --approval <a.json> --run-dir <d>
  [--db-env dev|prod]`, plus a `--validate-only` mode that re-derives PASS/FAIL
  from raw artifacts + a fresh MySQL read, never trusting `summary.json`
  (`:410-424`, `:514-737`).
- **Approval schema** (`:3-10`) — `{base_url, require_non_8000, max_total_usd,
  forbidden_phrases, instance_identity?, questions:[{id, family, name, route?,
  turns:[{label, query, pass_criteria:[{field,op,value}]}]}]}`. It reuses
  chat_nextseek's `Variant`/`Turn`/`PassCriterion` + `check_pass` DSL
  (`:140-143`, `:446-450`).
- **What it needs** — a running UI at `base_url` (a non-8000 port can be
  required, `:426-429`), Playwright, MySQL creds (host/user/password via
  `MYSQL_*` env, `:165-204`), a real Bedrock budget, and a **hand-authored,
  hash-frozen approval artifact** (any drift fails the run, `:541-544`).
- **What it asserts per question** (`:454-491`) — `status == "passed"`, no
  `forbidden_phrases` in the reply, `session_id` present, **CC cost > 0 on the CC
  route** (fail-closed; NS route carries no cost), and `route_mismatch` (declared
  vs. actual route). All artifacts are sha256-manifested so `--validate-only`
  is tamper-evident.
- **Route awareness** — **yes, this is the only file-level routing gate.** It
  detects each turn's route from the persisted `query_complete.data` **shape**
  (`_detect_route_from_data`, `:313-321`: `"debug"` ⇒ `ns`;
  `"total_cost_usd"`/`"cc_session_id"` ⇒ `cc`), and if a question **declares** an
  expected `route` and the actual differs, that is a **routing regression that
  fails the question** (`route_mismatch`, `:463`, `:632`). But note two limits:
  the declared route is **optional per question**, and it asserts on the
  **response shape**, not on the parser/router's decision object directly.
- **Paid/slow** — yes (real browser + real CC turns). Hermetically wrapped by
  `tests/test_full_ui_e2e.py` (below) with fake runner/DB — no spend.

### 1c. `tests/cc_matrix_gate_harness.py` + `CCCapabilityGateMatrix` — DORMANT direct-exec matrix

- **What it is** — the **superseded** capability gate. Spawns a **dedicated idle
  executor** container (`dmac-cc-matrix-<run_id>`) built from the CC image and
  `docker exec`s all **9** `nextseek-*` shims **directly** — no Claude, no
  router, no LLM (`cc_matrix_gate_harness.py:1-37`, `build_op_argv` `:71-126`,
  `docker_exec_op` `:192-216`). Its own docstring in `step7_per_op_evidence.py:5-8`
  says it "proved the shims run when invoked by hand; it never routed through CC".
- **Entry point** — the module is pure support code, imported **only** by
  `test_cc_realstack.py`'s `CCCapabilityGateMatrix` test (`test_cc_realstack.py:468-685`),
  which runs only under `RUN_REALSTACK=1`. It spawns the executor, execs the 9
  ops using kwargs from a committed catalog, runs the trusted sweep, and writes
  `plugin_ops_matrix.json` + companions, then re-checks with
  `step7_validate_run` (`test_cc_realstack.py:516-685`).
- **The input set** — `STEP7-UPSTREAM-EXERCISE-CATALOG.json` (per-op
  `{bin_op, inputs, exercise_id, upstream_ref}`) + `instance_binding.json`
  (`{project_id, project_title, reference_uids}`), loaded via
  `step7_gate_catalog.build_op_kwargs_from_catalog` (`step7_gate_catalog.py:88-118`).
  This is a **shim-argument** set, not a user-query set — the agent/router is
  bypassed.
- **What it needs** — a live deployed stack + `/var/run/docker.sock` +
  `docker-py` + the instance-binding catalog. The **pure-logic** pieces (argv
  build, row shaping, fixture payloads) are unit-tested hermetically in
  `tests/test_cc_matrix_gate_harness.py`; the docker/HTTP pieces run only under
  `RUN_REALSTACK=1`.
- **What it asserts** — every op's `exit_code != 7` (TRANSPORT_ERROR /
  missing backend) (`test_cc_realstack.py:673-675`), per-op ledger cost > 0
  (`:574-591`), no shared creds in the executor env, and the SPEC-7 bundle shape.
  It proves **op liveness against the real backend**, not routing.

### 1d. `docker/cc-runtime/tools/e2e/judge_runner.py` — in-image BAML UI-judge (partially vendored)

- **What it is** — the in-image entrypoint for the BAML `JudgeUITranscript`
  function (`judge_runner.py:1-22`). It is `docker run` **inside** a
  `dmac-assistant:e2e-<date>` image, mounting an evidence dir, and grades one
  recorded transcript with an **LLM-as-judge** over `{query, pass_criteria,
  ui_answer}` (`:117-146`).
- **Model** — Gemini via `NEXTSEEK_EVALUATOR_MODE=gcp` + `GCP_API_KEY` **only**;
  it deliberately **refuses to read `AWS_BEARER_TOKEN_BEDROCK`** (`:18-22`,
  `:33-53`). Output is `{"verdict","reasoning","model"}` on stdout.
- **Layer** — it judges a **recorded** transcript; it does **not** route, drive
  turns, or touch the UI. It's the scoring stage ("T5") of a larger "T1..T6" e2e
  pipeline.
- **Critical gap** — only the **in-image judge** is vendored here. The host-side
  driver that would produce `queries.json` (T1), capture `ui_answer` (T6), and
  invoke this judge (`judge_query`, T3) is **not in this repo**
  (`:68-75` reference `queries.json` and a `judge_query` caller that live
  elsewhere). So this e2e pipeline is **not runnable from `dev-v3-merge` alone**.

### 1e. `tests/test_cc_realstack.py::CCRealStackAcceptance` — the real routing-aware paid harness (PAID)

This is the single most important file for the routing question (details in §3).
`RUN_REALSTACK=1` Django test (`test_cc_realstack.py:1-25`, `:93`), needs the
full deployed stack (`dmac-assistant:poc` image, `dmac-cc-net`,
`dmac-bedrock-proxy`, docker socket). Six tests: `test_01` (real BAML router →
`container_cc` + Opus), `test_02` (real Opus turn via proxy + publish +
cred-isolation), `test_03` S1 / `test_04` S2 / `test_05` S3 / `test_06` T17 —
the cross-mode + referent-scoping scenarios. Paid + slow (210s joins).

### 1f. The hermetic unit suite (zero spend — runs in the normal `pytest` run)

~70 `test_*.py` under `nextseek_api/cc_assistant/tests/`. The ones relevant to
routing/memory/gates:

- **`test_router_heuristic.py`** — tests **only the keyword fallback** used when
  BAML is unavailable (`_heuristic`), and that `decide()` falls back to it when
  `_baml_decision` returns `None` (monkeypatched, zero spend). It does **not**
  test the real BAML NS-vs-CC decision (`test_router_heuristic.py:1-72`).
- **`test_decide_route_pipeline_gate.py`** — the `_decide_route` precedence
  wrapper: an active `pipeline_agent` forces NS (`decide` must not even be
  called), `force_cc` beats an active pipeline, and history is threaded through
  to `decide` (`test_decide_route_pipeline_gate.py:16-42`).
- **`test_router_context.py`, `test_route_override.py`, `test_baml_router_schema.py`,
  `test_router_history_plumbing.py`** — router-history projection + override +
  BAML schema plumbing (see inventory §1g).
- **`test_ns_turn_context.py`, `test_cc_turn_context.py`, `test_ns_digest.py`,
  `test_recall_op.py`, `test_query_op.py`** — the cross-mode memory data plumbing
  (the NS→CC and within-chat digest). E.g. `test_cc_turn_context.py` proves
  `build_cc_contexts` projects only answered CC turns, skips NS/errored/malformed,
  and truncates long replies.
- **`test_full_ui_e2e.py`** (60+ tests) and **`test_cc_matrix_gate_harness.py`**
  — hermetic wrappers of the two paid scripts (fake runner / fake docker), which
  prove the harnesses' *plumbing* (gating logic, `--validate-only` recompute,
  argv/row shaping) without any spend.
- **`docker/cc-runtime/tests/unit/test_batch_upload_*.py`** — hermetic unit tests
  for the in-image batch-upload payload builder/runner/skill-contract.

> Inventory note: a fan-out sweep of the hermetic router/memory/gate tests and
> the cc-runtime unit tests is folded into §1g below.

### 1g. Hermetic router/memory/gate + cc-runtime unit-test detail

All of the following are **hermetic** (no network, no docker socket, no DB, no
LLM spend). They test the *plumbing* around routing/memory, never a live LLM
decision.

**Router-decision plumbing (Django app, `nextseek_api/cc_assistant/tests/`):**

- `test_router_context.py` — 11 tests of `router_context.build_history`/
  `truncate_utf8`: per-field UTF-8 byte caps (query 512 / reply 1024 / error 256),
  last-5-only, four turn kinds, legacy `router_choice` derivation, malformed
  entries never raise. (Fixture NS turn carries `result_summary.count=139` +
  `D.SEQ-*` uids.)
- `test_route_override.py` — the admin-only `force_route` field + `_decide_route`
  gate: staff/superuser can force `ns`/`cc`; **non-admin force is dropped** and
  falls back to the router; `force_cc` endpoint forces CC for anyone. Router
  `decide`/`_resolve_cc_model_id` are stubbed offline.
- `test_baml_router_schema.py` — pins the **two `router.baml` copies
  byte-identical** (`dmac_assistant/baml_src/router.baml` ==
  `docker/cc-runtime/baml_src/router.baml`), the `HistoryTurn` class + 8 fields,
  and a byte-exact snapshot of the prompt's history+CURRENT region. This
  version-locks the router prompt/schema but exercises **no** decision.
- `test_router_history_plumbing.py` — history is forwarded through
  `decide()`→`_baml_decision()`, heuristic ignores history, real `reasoning`
  surfaced with `source="baml"`, and an **AST check** that the live service call
  site binds `history=build_history(...)`.
- `test_router_heuristic.py` + `test_decide_route_pipeline_gate.py` — the keyword
  fallback + precedence wrapper (covered in §1f/§3).

**Cross-mode memory plumbing:** `test_ns_turn_context.py` (`from_bundle`/
`build_contexts`, incl. a **7-shape `_total_and_rows` parametrized table** and a
**parity check against chat_nextseek's `_extract_total_and_rows`**),
`test_cc_turn_context.py`, `test_ns_digest.py` (digest render + a `nextseek-recall
--turn N` pointer + an AST proof the service composes via the pure functions),
`test_recall_op.py` / `test_query_op.py` (the in-image `nextseek-recall`/
`nextseek-query` dispatch: explicit-turn recall, **no "latest" fallback**, no
residue on error, runs in the live session).

**Gate/validator plumbing (hermetic wrappers of the paid harnesses):**

- `test_cc_matrix_gate_harness.py` — 34 tests of the matrix's pure logic (argv
  per op, executor env goes through `cc_engine.build_agent_environment` and
  excludes shared creds, row shaping, seeded-fixture creation via a fake HTTP
  layer, plus source-text "drift-pins" against `models.py`/`services/*.py`). One
  `xfail` (`build_op_argv_covers_every_bin_op`).
- `test_validate_cc_acceptance.py` — proves the zero-spend acceptance validator
  over synthetic pass/fail bundles; notably its **`router_is_baml` check fails on
  a heuristic route** and `cost_under_cap` fails over budget — i.e. the validator
  *encodes* the routing + cost invariants that the paid `test_cc_realstack`
  produces evidence for.
- `test_full_ui_e2e.py` — 60+ tests of the full-UI harness plumbing (fake runner,
  route-mismatch gate, fail-closed cost, `--validate-only` tamper detection).

**cc-runtime image unit suite (`docker/cc-runtime/tests/unit/`):** runs with
**`--disable-socket`** and a **`--cov-fail-under=95`** gate
(`docker/cc-runtime/pyproject.toml:62-73`), with opt-in markers
`integration`/`live`/`live_bridge`/`live_docker`/`slow` for the live suites.
`test_batch_upload_{client,runner,payload,models,extract,deps,shims}.py` +
`test_batch_upload_skill_contract.py` hermetically test the in-image
**batch-upload** shim/client/payload builder and lint the `SKILL.md` contract
(forbidden `start`/`upload`/`submit` flows). A fixture corpus lives under
`tests/unit/fixtures/ns/`. Note `build_context/plugins/nextseek/bin/tests/
test_dispatch_pipeline.py` sits **outside `testpaths=["tests"]`**, so it is a
separate root not collected by the default runtime `pytest` run.

**Confirmed negatives / docs:** `dmac_assistant/` ships **no tests and no pytest
config** (its router is tested only externally by the Group-A files above). There
is **no** `CLAUDE.md`/`README.md`/`DEPLOY.md` at `docker/cc-runtime/` root;
`container/CLAUDE.md` is the in-container **agent system prompt** (write-safety,
skills, stop-after-2), not a how-to-test doc. The runtime's how-to-test is only
the pytest config in `docker/cc-runtime/pyproject.toml` and the outer
`CLAUDE.md` "Testing" section.

---

## 2. What layer each harness exercises

| Harness | Router (NS↔CC)? | CC turn via container? | HTTP/UI path? | Backend (real data)? | Spend |
|---|---|---|---|---|---|
| `step7_gate3d_per_op.py` | **No** — forces CC (`session_id=None`, calls `run_cc_turn` directly) | **Yes** (ephemeral agent per op) | No (in-process engine call) | Yes (ops hit live REST/graph/sidecar) | **Paid** |
| `full_ui_e2e.py` | **Partial** — detects route from response shape; optional per-question declared-route gate | Yes (whichever the router picks) | **Yes** (real browser → chat UI) | Yes | **Paid** |
| `cc_matrix_gate_harness` / `CCCapabilityGateMatrix` | **No** — bypasses CC + router; direct `docker exec` of shims | No (idle executor, not a Claude turn) | No | Yes | Paid (ledger) |
| `judge_runner.py` | No | No (judges a recorded transcript) | No | No (LLM judge only) | Paid (Gemini) |
| `test_cc_realstack.py` (S1/S2/S3/T17 + t01) | **Yes** — asserts on the real router decision + cross-mode handoff | Yes | Partial: `test_01/02` in-process; S1/S2/S3/T17 via **live gunicorn HTTP** (`/cc-assistant/query/async`, `test_cc_realstack.py:306-329`) | Yes | **Paid** |
| hermetic `test_router_*` / `test_*_context` | Decision **precedence + heuristic + history plumbing only** (never the real BAML LLM decision) | No | No | No | Free |

Precise reading: a **single CC op in isolation, forced** = `step7_gate3d_per_op`.
A **full CC turn through the container, router-chosen, over HTTP/UI** =
`full_ui_e2e` (UI) and `test_cc_realstack` S1–T17 (HTTP). **Shims in isolation,
no CC** = the matrix. **Nothing hermetic** exercises a real router LLM decision.

---

## 3. CRITICAL — does anything test the top-level router / NS-vs-CC routing?

**Yes, but only in the paid, gated `test_cc_realstack.py`, plus an optional
response-shape gate in `full_ui_e2e.py`. Nothing hermetic asserts the real BAML
router's NS-vs-CC decision, and the two per-op/matrix gates don't test routing
at all (one forces CC, the other bypasses CC).**

What actually asserts on routing / cross-mode handoff:

- **`test_01_real_baml_router_decides_container_cc`** (`test_cc_realstack.py:155-175`)
  — asserts `decision.source == "baml"` (proves the **real** router ran, not the
  heuristic), `decision.route == ROUTE_CC`, and `model_id == OPUS`. This is the
  one direct assertion on the router's decision object.

- **`test_03_s1_cross_turn_histogram`** (`:353-380`) — **the NS→CC handoff test
  (issue #32-adjacent).** Turn 1 (`"Find me all mice treated with NDMA"`) must
  route **NS** (`r1.route == ROUTE_NS`, `:359`); turn 2
  (`"Now create a histogram of the results…"`) on the **same session** must route
  **CC** (`r2.route == ROUTE_CC`, `:368`), and the CC reply must reference the
  **recall** artifact/manifest (`assertRegex(reply, r"nextseek-recall|recall.*manifest|/data/scratch")`,
  `:373-377`). This is exactly "NS→CC carrying prior-turn context".

- **`test_04_s2_compound_single_turn`** (`:382-412`) — a compound question routes
  **CC**, and the transcript shows a `nextseek-query`/`parse`/`api-read` op (CC→NS
  data pull).

- **`test_05_s3_referent_scoping`** (`:414-448`) — **the cross-turn "ask about
  those" test (issue #32b-adjacent).** Turn 1 finds NHP-seq samples; turn 2
  (`"Give me the unique counts of sex and species of all of those monkeys"`) must
  reference **turn-1's cardinality** (`assertIn(str(turn1_total), reply)`) and
  **must not** be scoped to the full 408-sample corpus
  (`assertNotIn("408", reply)`, `:439-448`).

- **`test_06_t17_multiturn_route_with_history`** (`:450-465`) — with history, a
  follow-up (`"Run rnaseq on these monkeys…"`) must route **NS** and
  `route_decided.reasoning` must reference prior context. This is the "follow-ups
  stay on NS + cite the prior turn" behavior the handoff calls "WORKS".

The router decision is emitted as a `route_decided` progress event
(`services/cc_assistant.py:333-336`) and these tests read it back from the live
progress stream (`_route_from_progress`, `test_cc_realstack.py:347-351`).

`full_ui_e2e.py` adds a **file-level** routing gate: `declared_route` vs. a route
recomputed from the response shape → `route_mismatch` fails the question
(`full_ui_e2e.py:452-463`, hermetically proven by
`test_declared_route_mismatch_fails`, `test_full_ui_e2e.py:511-527`). But it
infers the route from `query_complete.data`'s keys, not from the router object,
and only when a question opts in with a `route` field.

**What is NOT covered:** no hermetic test exercises the real BAML router's
NS-vs-CC choice (only the keyword `_heuristic` and the `_decide_route`
precedence wrapper are hermetic). `step7_gate3d_per_op` **forces** CC for every
op, so it asserts nothing about routing. `CCCapabilityGateMatrix` bypasses the
router entirely.

---

## 4. Coverage of issues #32 / #33

Issue definitions (from the 2026-07-24 handoff report, parent
BioMicroCenter/NExtSEEK #31/#32/#33; fork #10/#11):

> #32 (parent #10): "ask about last results" works at the routing level (parser →
> `ask_about_last_results`, correct `target_result_id`) but the recalled
> advanced_search bundle stores only `{id, uuid}` per sample, and the graph path
> stores **no** per-sample rows (`RETURN DISTINCT id/uuid/type LIMIT 250`). So
> "which of those are RNA-seq", "species of those", "write a CSV of those" all
> fail on missing data; **species additionally lives on the NHP PARENTS**.
>
> #33 (parent #33, fork #11): "Find NHP sequencing data" → REST advanced_search
> (139) vs. "Find sequencing data for non-human primates" → graph_query
> DERIVED_FROM (250, LIMIT-capped, no rows). **Same intent, two engines, two
> answers.**

### (a) #32a — child→parent traverse + parent-attribute aggregation

**Absent.** No harness asserts a child→parent traversal + parent-attribute
aggregate answer. The closest is `test_cc_realstack.py::test_05` (S3), which asks
for "**species** of all of those monkeys" — and species is precisely the
attribute the report says lives on the NHP **parents**. But S3 only asserts on
**cardinality reference** and the absence of "408" (`:439-448`); it never asserts
the actual per-sample **species/sex values** are correct. So even the one test
that touches parent attributes does not verify the traverse-and-aggregate result.
`OP_QUERIES` has a graph question (`"Show me all NHPs in the Impact project."`)
and a plan question, but `evaluate_op_row` only checks liveness/invocation, never
that parent attributes were traversed (`step7_per_op_evidence.py:242-310`).

### (b) #32b — recalled-bundle thinness / cross-turn "ask about last results"

**Partly present at the routing level, absent at the data level.**
- At the **parser routing** level, the deterministic **NS** catalog covers it:
  `chat_nextseek/e2e/catalog.json` has ~9 `pass_criteria` asserting
  `parser_plan.mode == "ask_about_last_results"` (e.g. `catalog.json:2512, 3604,
  9503…`), including a dedicated `refine_last_search + ask_about_last_results`
  family (`catalog.json:9287`). This is the "routing WORKS" half.
- At the **CC** level, `test_cc_realstack.py::test_05` (S3) is the only live
  assertion that a follow-up recall is scoped to the prior result set (§3).
- But **nothing asserts the recalled bundle is rich enough** to answer an
  attribute follow-up. The NS catalog's `pass_criteria` check `parser_plan`/
  `api_plan` shape, not the stored-bundle contents. The bundle-thinness itself
  (`{id,uuid}`-only advanced_search bundles; rowless graph bundles) has **no
  dedicated test**. The data shape is visible in the source: `ns_turn_context`
  materializes `sample_uids` and `columns` from the bundle rows
  (`ns_turn_context.py:89-99`) — if the bundle stored only `{id,uuid}`, `columns`
  degrades to `["id","uuid"]`, and no test guards against that.

### (c) #33 — REST-vs-graph routing split

**Absent.** No harness asserts that two phrasings of the same intent ("NHP
sequencing data" vs. "sequencing data for non-human primates") route to the same
engine / return the same count. This is a chat_nextseek/NS-parser routing
consistency property, and neither the CC gates nor the NS catalog assert
cross-engine equivalence. The NS catalog tests each variant's route in
**isolation** (its own `parser_plan.mode`/`api_plan.endpoint`), never that two
variants **agree**.

**Summary:** #32b's *routing* is covered (NS catalog + S3); #32a, #32b's
*data-richness*, and #33 are all **uncovered**. These are exactly the "data-shape"
gaps the handoff flagged as the next real work.

---

## 5. Why it's "not unified and not routinely run" (#31)

#31's own words: "comprehensive testing (unify NS catalog + CC gates + UI e2e)".
Concretely, the friction is:

1. **Three harnesses, three entry points, three execution contexts.**
   - `step7_gate3d_per_op.py` — runs **inside** the live `nextseek` container via
     `docker exec`, needs `STEP7_LLM_LEDGER=1`, budget env, SEEK creds, a bundle
     dir (`step7_gate3d_per_op.py:12-19`).
   - `full_ui_e2e.py` — runs **off-box**, needs a live browser (Playwright), a
     hand-authored **hash-frozen `approval.json`**, MySQL creds, a non-8000
     `base_url` (`full_ui_e2e.py:12-14`, `:426-429`).
   - `CCCapabilityGateMatrix` — a `RUN_REALSTACK=1` **Django test**, needs the
     docker socket, `dmac-assistant:poc`, `dmac-cc-net`, the proxy, and a
     committed **`instance_binding.json` + exercise catalog**
     (`test_cc_realstack.py:468-508`).
   - And the **NS** side is a *fourth* harness entirely: `cd chat_nextseek &&
     uv run e2e.py` over `catalog.json` (366 variants) with its own runner.
2. **All the CC gates are PAID and SLOW.** Every CC-routed turn is a real Opus
   turn behind a fail-closed `cost > 0` gate; the 180s per-turn hard cap makes a
   9-op matrix "structurally unachievable inside one forced-CC turn"
   (`cc_matrix_gate_harness.py:7-10`) and forces the dedicated-executor design.
3. **Heavy manual setup / bespoke input artifacts.** Each harness needs a
   different curated input: `OP_QUERIES` (per-op), a frozen `approval.json`
   (full-UI), `instance_binding.json` + `STEP7-UPSTREAM-EXERCISE-CATALOG.json`
   (matrix). None share a schema.
4. **Data-dependent on the seed.** `OP_QUERIES` had to be edited because the GBM
   question "has no matching study on dev (GBM exists only on prod)"
   (`step7_gate3d_per_op.py:66-73`) — questions silently return 0 unless matched
   to the live seed.
5. **Brittle live plumbing.** Ephemeral per-turn containers; the 180s cap can
   kill a turn mid-run (hence transcript cost-recovery,
   `step7_per_op_evidence.py:155-188`); route detection is response-shape-based;
   the DEV-vs-PROD DB and non-8000 port must be pinned to avoid validating a
   stale instance (`full_ui_e2e.py:104-121`).
6. **The judge e2e is only half-vendored** — `judge_runner.py` is the in-image
   scorer, but its host-side driver (`queries.json`/T1, `ui_answer`/T6,
   `judge_query`/T3) is not in this repo (`judge_runner.py:68-75`), so it can't
   be run end-to-end from here.
7. **Gates are "authored now, executed later."** `CCCapabilityGateMatrix` is
   explicitly "written now (Task 15); EXECUTED only later … with the user's
   sign-off" (`test_cc_realstack.py:476-481`) — i.e. designed to sit dormant.

Net: there is no single command, no shared input schema, no shared runner, and
every live path costs money and needs a bespoke frozen artifact + a fully
deployed stack. That is why it "is not unified and not routinely run".

---

## 6. Reusability — is there an input→expected structure to fold into a unified, router-aware harness?

**Yes — and `full_ui_e2e.py` already prototypes the router-aware version of the
NS catalog shape. That is the natural seam.**

Three input shapes exist today:

| Source | Shape | Route-aware? | Expected-output model |
|---|---|---|---|
| `chat_nextseek/e2e/catalog.json` | `{families:{<f>:{variants:[{id,name,tags,requires_env,turns:[{label,query,pass_criteria:[{field,op,value}]}]}]}}}` — 366 variants | **No** `route` field | Rich **NS-internal** criteria DSL (`parser_plan.mode`, `entity_sampletype_codes`, `api_plan.endpoint`, `api_ok`) — deterministic, correctness-checking |
| `full_ui_e2e.py` approval `questions` | `{id,family,name,route?,turns:[{label,query,pass_criteria:[{field,op,value}]}]}` | **Yes** — optional per-question `route` (`ns`/`cc`) + `route_mismatch` gate | Same `PassCriterion` DSL **reused verbatim** (`full_ui_e2e.py:140-143`) + forbidden-phrase + fail-closed CC cost |
| `step7_gate3d_per_op` `OP_QUERIES` | `dict[op → query]` | No (forces CC) | **Implicit**: liveness only (cost>0, op invoked, non-error) — *not* correctness |
| matrix `STEP7-UPSTREAM-EXERCISE-CATALOG.json` | `[{bin_op, inputs, exercise_id, upstream_ref}]` + `instance_binding.json` | No (bypasses CC) | shim `exit_code != 7` + ledger cost |

Key observation: **`full_ui_e2e.py` already extends chat_nextseek's
`Variant`/`Turn`/`PassCriterion` with a `route` field and a route-mismatch gate,
reusing `check_pass`.** So a unified router-aware harness is a small delta, not a
rewrite:

- Adopt **`catalog.json`'s Variant/Turn/PassCriterion schema** as the single
  source of truth (it already carries multi-turn `turns`, so cross-mode follow-ups
  like S1/S3 fit natively).
- Add an **optional `route: "ns"|"cc"|"unrelated"` per variant** (already
  understood by `full_ui_e2e`'s `declared_route`/`route_mismatch`), turning the
  catalog into a router-level oracle.
- Fold **`OP_QUERIES`** in as **CC-route variants** whose `pass_criteria` assert
  op invocation (the evidence already exists as `OpInvocation`/`OpRow` in
  `step7_per_op_evidence.py`).
- For #32/#33, add `pass_criteria` that assert on **recalled-bundle richness**
  and **cross-engine count agreement** — the two properties currently unmeasured.

The reusable spine is thus: **catalog.json's variant/criteria DSL + full_ui_e2e's
route field & route detector + step7's op-invocation evidence**, run by one
runner that can dispatch a variant either through the deterministic NS path
(cheap, hermetic-ish) or the paid CC path based on its declared route. The main
open design question is cost control: the NS catalog is free/deterministic while
every CC variant is a paid Opus turn, so a unified harness needs a
budget-tiered / smoke-vs-full split (the `tags: ["smoke","regression"]` already
in `catalog.json` is a ready lever).

---

## Appendix — key file:line references

- Router library: `dmac_assistant/src/dmac_assistant/router/agent.py:100-148`
  (`RouterAgent.route`, BAML `RouteQuery`, `_fallback_decision` → `ContainerCC/Sonnet`).
- Bridge router: `nextseek_api/cc_assistant/router.py:110-196`
  (`_heuristic`, `_baml_decision`, `decide`); `ROUTE_*` = `router.py:34-37`.
- Route wiring: `nextseek_api/services/cc_assistant.py:203-244` (`_decide_route`),
  `:333-336` (`route_decided` event).
- Router history: `nextseek_api/cc_assistant/router_context.py:38-104`
  (`HistoryTurn`, `build_history`, caps + `result_count`/`sample_uids`).
- NS→CC memory: `ns_turn_context.py:71-128` (`from_bundle`/`build_contexts`),
  `cc_turn_context.py` (`build_cc_contexts`), `ns_digest.py:14-90` (digest render).
- Per-op harness: `scripts/step7_gate3d_per_op.py` (OP_QUERIES `:63-84`);
  evidence `step7_per_op_evidence.py:242-332`.
- Full-UI harness: `scripts/full_ui_e2e.py` (route detect `:313-340`, gates
  `:454-491`, validate `:514-737`).
- Matrix harness: `tests/cc_matrix_gate_harness.py` + `test_cc_realstack.py:468-685`;
  catalog loader `step7_gate_catalog.py:69-118`.
- In-image judge: `docker/cc-runtime/tools/e2e/judge_runner.py`.
- In-container agent contract: `docker/cc-runtime/container/CLAUDE.md`.
</content>
</invoke>
