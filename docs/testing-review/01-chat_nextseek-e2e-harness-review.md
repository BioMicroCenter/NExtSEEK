# Review: chat_nextseek E2E test harness

Scope: `chat_nextseek/e2e.py`, `chat_nextseek/e2e/**`, `chat_nextseek/tests/**`,
`chat_nextseek/conftest.py`, `chat_nextseek/README.md`, `chat_nextseek/CLAUDE.md`.
Read-only analysis feeding a design discussion on unifying the NS + CC harnesses
and adding router-level tests. All paths are relative to
`/home/cdemu/code/dmac/docker/dev-v3-merge/chat_nextseek/`.

**Headline finding:** the harness is a strong, Pydantic-validated,
input→expected-output catalog that asserts the **NS-internal parser mode** on
almost every variant (358 `parser_plan.mode` assertions). But it never asserts
the **top-level NS-vs-CC route**, no such mode even exists in the schema it
checks, and it bakes in a phrasing-dependent graph-vs-REST contradiction rather
than testing routing consistency. It is highly reusable as the substrate for a
unified, router-aware harness.

---

## 1. What it is / architecture

### 1.1 Catalog structure

The corpus is one JSON file, `e2e/catalog.json` (324 KB), validated by Pydantic
models in `e2e/catalog.py:11-45`:

```
Catalog { version, families: dict[str, Family] }
  Family { description, variants: list[Variant] }
    Variant { family, id, name, tags[], requires_env[], turns: list[Turn] }
      Turn { label, query, pass_criteria: list[PassCriterion] }
        PassCriterion { field, op, value }
```

`PassCriterion.op` is a closed enum (`e2e/catalog.py:13`):
`eq | contains | nonempty | true | gte | lte | mentions | matches_re | trio_match`.

A concrete leaf (the cheapest smoke test, `e2e/catalog.json` `advanced.basic_ndma`):

```json
{ "label": "main", "query": "Find me mice treated with NDMA.",
  "pass_criteria": [
    {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
    {"field": "entity_sampletype_codes", "op": "contains", "value": "MUS"},
    {"field": "api_plan.endpoint", "op": "contains", "value": "advanced_search"},
    {"field": "api_plan.requestBody.filter_searchText", "op": "contains", "value": "NDMA"},
    {"field": "api_ok", "op": "true"} ] }
```

So an entry is: **one natural-language query → a list of AND-ed field/op/value
assertions against a debug dict** (plus a few session/reply/filesystem field
families). A `Variant` may carry multiple `Turn`s to model multi-turn
conversations (56 variants are multi-turn), and turns in a variant share one
session.

### 1.2 The 11 families (actual counts, from parsing `catalog.json`)

| Family | # | What it routes to | Asserted `parser_plan.mode` |
|---|---:|---|---|
| `search_advanced` | 68 | `/samples/advanced_search/` (keyword+sampletype) | `new_search` |
| `search_tree` | 15 | `/sample-tree/{uid}/tree/` lineage | `new_search` (+1 recall) |
| `search_parents_by_child` | 18 | `parents_by_child_types` (child→parent) | `new_search` |
| `search_retrieve` | 19 | `/admin/samples/retrieve/`, `/samples/{uid}/` | `new_search` (+1 recall) |
| `graph_query` | 60 | Neo4j Cypher via graph_agent | `graph_query` |
| `reporting` | 64 | reporter SQL summaries + GEO/SRA/PRIDE artifacts | `reporter` |
| `pipeline_nfcore` | 22 | pipeline_agent → samplesheet → Tower | `new_search` then `pipeline_agent.*` |
| `system_question` | 27 | capability/definition lookups | `system_question` |
| `unsupported` | 7 | out-of-scope (weather, charts, stats) | `unsupported` |
| `writes_unsupported` | 19 | destructive admin (create/register/update) | `unsupported` |
| `refine_and_recall` | 47 | multi-turn refine + ask-about-last-results | `new_search`→`refine_last_search`/`ask_about_last_results` |

**Total = 366 variants** (`README.md:119` and `CLAUDE.md` both say "362"; the
catalog has grown past the docs — `search_advanced` +2, `graph_query` +1, and
the total is off by 4; see §5 doc-drift).

### 1.3 How `e2e.py` loads + executes

`e2e.py` is a thin argparse front door (`e2e.py:30-133`). Non-run subcommands:
`--init-env` (`:71-81`), `--list` (`:83-100`), `--report` (`:102-108`, regenerate
HTML from a prior run). Otherwise it delegates to `e2e.runner.run_main`
(`e2e.py:114-129`).

`run_main` (`e2e/runner.py:169-297`):
1. `load_catalog(catalog_path)` → validated `Catalog` (`:193`).
2. Build the **plan** (which variants run this invocation), by precedence
   (`:196-217`): `--rerun` manifest → `--variant`/`--spot` → `--family` → else
   **seeded sample** via `sample(cat, ratio, rng)` (`:216`). Default `ratio=0.33`
   (`e2e.py:32`); `--ratio full` = 1.0 (`e2e.py:24-27`).
3. **CLI tier** (`:232-242`): for each planned variant call `run_variant`, collect
   a `ManifestEntry`.
4. **Playwright tier** (`:245-279`): filter the plan to `"playwright" in v.tags`
   (`:247`), probe the UI (`:252`), and if reachable run `run_variant_browser`
   per tagged variant; otherwise mark them `skipped` (`:274-279`).
5. Write `manifest.json` (`:283-289`) + `report.html` (`:291`). Exit code 0 iff
   zero failures (`:297`).

`run_variant` (`e2e/runner.py:79-163`) is the per-variant core:
- Fresh session `SQLiteSessionState(config.SESSION_DB_PATH, "e2e-<id>")` (`:94`,
  `:44-46`); the same session object is reused across turns (multi-turn state).
- For each turn (`:101-155`): optional pacing sleep (`:106-107`, default 15 s
  between turns — `e2e.py:48`), reset per-run logging (`:109`), then
  **`result = run_query(session, config, turn.query)`** (`:113`).
- Persist artifacts per turn: `query.txt`, `reply.txt`, `debug.json`, and the
  orchestrator's captured run-root dir (`:132-137`).
- **Evaluate**: `check_pass(result["debug"], turn.pass_criteria, session=…,
  last_reply=…, run_root=…)` (`:139-145`).

### 1.4 How assertions are defined/evaluated

There are **no golden files and no LLM-judge.** Evaluation is a **field-resolver +
op DSL** in `e2e/criteria.py`:

- `resolve_field` (`:55-211`) maps a dotted `field` to a value. Field families:
  - top-level `debug` dot-navigation with a `sample_type`↔`sampletype` alias
    (`:192-211`);
  - convenience aliases `entity_sampletype_codes`, `entity_assay_codes`,
    `graph_cypher`, `neo4j_ok`, `api_ok`, etc. (`:179-190`);
  - session-state families `wizard.*`, `pipeline_agent.*`, `chat_log.*`,
    `results_history.length` (`:115-169`);
  - `last_reply` (HTML-stripped, `:171-172`), `last_target_result_id` (`:174-176`);
  - **filesystem** family `api_artifact.<file>[.rows_gte]` — checks a real file
    the run wrote under `run_root/files/`, counting CSV/XLSX rows (`:26-52`,
    `:111-112`);
  - **browser-only** families `ui_text.*` and `mysql_chat_log.*` (`:77-109`).
- `_check_one` (`:214-251`) implements the ops. Note the semantics:
  `eq` exact; `contains` list-membership OR substring; `nonempty` truthiness;
  `true` `is True`; `gte`/`lte` numeric coercion; `mentions` case-insensitive
  substring; `matches_re` `re.search` (IGNORECASE); `trio_match` special-cased in
  `check_pass` (`:283-297`).

So assertions are **structural / substring / regex / count**, never exact
expected-output. `api_ok=true` means "the REST call returned ok", **not** "the
right rows came back"; `graph_cypher` is checked only `nonempty` or
`contains '<project token>'`, **never** for correctness or a row count.

### 1.5 The `--playwright` browser tier vs the default (CLI) tier

Two tiers, dispatched from the same plan (`README.md:107`, `:155-186`):

- **CLI tier (default, "Front-1")** — `e2e/runner.py`. Calls
  `chat_nextseek.orchestrator.run_query` **in-process** (`e2e/runner.py:13,113`).
  No browser, no Django endpoint. This is what `--ratio`/`--family`/`--variant`/
  `--rerun` drive; `cli.py -st`/`-ft` are shims over it (`CLAUDE.md` Testing).
- **Browser tier ("Front-2", Phase E)** — `e2e/playwright/runner.py`
  (`run_variant_browser`, `:100-333`). Runs only variants tagged `"playwright"`
  — **exactly 4** today: `advanced.basic_ndma`, `report.geo_submission`,
  `pipeline.activation_rnaseq`, `refrec.refine_to_cd8`. It drives the **real
  Django chat UI** at `localhost:8000/8100` via `data-testid` selectors
  (`e2e/playwright/pages.py`), transported by **HTTP polling** of
  `GET /nextseek_api/cc-assistant/tasks/{id}/progress/` (`e2e/playwright/poll.py:10`).
  It additionally reads the **MySQL `chat_log`** (`e2e/playwright/mysql.py`) and
  can assert the **UI ≡ console ≡ MySQL "trio"** (`e2e/playwright/trio.py:22-59`).
  `--playwright` implies `--no-cli` (`e2e.py:110-112`); `--spot <id>`, `--headed`,
  `--video` scope/inspect it.

### 1.6 Infra it needs

- **CLI tier**: a fully running NExtSEEK stack reachable from the process —
  `run_query` makes **real REST calls** to `advanced_search`/etc. (asserts
  `api_ok`), **real Neo4j** queries (asserts `neo4j_ok`), and **real LLM API
  calls** for entity/parser/graph/reporter agents (default profile mixes GCP
  Gemini + Anthropic via Bedrock — `README.md:14-20`, `CLAUDE.md` Tech Stack).
  So it needs a **seeded DB + Neo4j graph + populated `.env` + real API budget**.
  `--init-env` generates `.env` from sibling docker files (`e2e/import_env.py`),
  defaulting `API_USER/PASS=demo/demopassword` and forcing SQLite session storage.
  Pacing (`--pace 15`) throttles LLM spend/rate-limits. Not a bare instance.
- **Browser tier**: additionally a **rebuilt container** carrying the
  `data-testid` frontend, a reachable UI, Chromium, and (for trio) MySQL creds
  (`README.md:172-176`). The runner gates on a `HEAD /nextseek_api/assistant/me/`
  probe (`e2e/runner.py:29-41`); unreachable UI ⇒ `skipped`, not failed.
- **Env-gated variants**: only `pipeline.tower_submit` declares
  `requires_env=["TOWER_ACCESS_TOKEN","TOWER_WORKSPACE_ID"]`; the sampler drops
  it when unset (`e2e/sampler.py:10-13`).

### 1.7 Trace: one entry → an assertion

`advanced.basic_ndma` (§1.1) →
`run_main` samples/plans it → `run_variant` (`runner.py:79`) makes a session and
calls `run_query(session, config, "Find me mice treated with NDMA.")`
(`runner.py:113`) → orchestrator runs catalog-shortlist → `entity_agent` →
`parser_agent` and returns `{"reply", "debug"}` where
`debug["parser_plan"]["mode"]` is the parser's decision (`orchestrator.py:456-467`)
→ back in `run_variant`, `check_pass(debug, criteria, …)` (`runner.py:139`) →
for criterion `{parser_plan.mode, eq, new_search}`, `resolve_field` dot-navigates
`debug["parser_plan"]["mode"]` (`criteria.py:192-211`) and `_check_one` does
`actual == "new_search"` (`criteria.py:215-217`) → all five criteria AND-ed
(`criteria.py:311`) → variant `passed`, written to `manifest.json` + `report.html`.

---

## 2. What layer it actually exercises

**CLI tier: the in-process NS orchestrator, entered below the top-level router.**
The single entry point is `chat_nextseek.orchestrator.run_query`
(`e2e/runner.py:13,113`). `run_query` (`orchestrator.py:382-483`) runs the full
NS pipeline: catalog shortlist → `entity_agent` → **`parser_agent`** (the
NS-internal router, `:456`) → dispatch on `plan.mode` (`:459`, `:483` onward) into
REST (`api` agent), Neo4j (`graph`), reporter, system, memory/recall, or
pipeline_agent. It hits the **real REST + Neo4j + LLM** stack but **does not go
through any HTTP endpoint or the Django app** — it imports the package and calls
the function. This is the NS engine's own front door, which sits **one level
below** the top-level NS-vs-CC router.

**Browser tier: the real HTTP endpoint, through the top-level router.**
`run_variant_browser` drives the merged chat UI, which POSTs to
`cc-assistant/query/async`, "whose BAML router (`nextseek_api/cc_assistant/router.py`)
dispatches each turn to either" the NS pipeline or the CC engine
(`e2e/playwright/poll.py:24-42`). So the browser tier **does traverse the
top-level router** — but only for the 4 tagged variants, and (critically) it
does not assert on the routing decision (§3).

---

## 3. CRITICAL — does it test the top-level router?

**It tests the NS-internal parser's mode decision extensively; it does NOT test
the top-level NS-vs-CC route at all.**

**(a) NS-internal parser mode IS asserted — heavily.** `parser_plan.mode` is the
single most common criterion: **358** of the ~1,050 criteria assert it, spanning
all 7 NS modes:

```
eq new_search              131      eq system_question        27
eq reporter                 64      eq unsupported            26
eq graph_query              60      eq refine_last_search     15
eq ask_about_last_results   35
```

Every family pins the mode it expects (§1.2). So "which NS engine handles this
query" — `new_search` vs `graph_query` vs `reporter` vs `ask_about_last_results`
vs `refine_last_search` vs `system_question` vs `unsupported` — **is a
first-class, per-variant assertion.** This is genuine router coverage, just of
the *inner* router.

**(b) The top-level NS-vs-CC decision is never asserted, and the schema being
checked has no CC mode.** The mode enum the harness checks is closed
(`schemas/router.py:29-31`):

```
# Valid modes: "new_search" | "refine_last_search" | "ask_about_last_results" |
#              "system_question" | "reporter" | "graph_query" | "unsupported"
```

There is **no `cc` / `handoff` / `container_cc` mode** in `ParserPlan`, no such
value anywhere in `catalog.json` (grep: 0 hits), and no `route` field family in
the criteria resolver (`criteria.py` has zero `route` handling). The top-level
BAML router that actually chooses NS-vs-CC lives in
`nextseek_api/cc_assistant/router.py` — **outside** `chat_nextseek` and
**above** `run_query`, so the CLI tier can never see it.

**(c) The browser tier observes the route but asserts nothing on it.**
`e2e/playwright/poll.py:117-129` (`detect_route_from_data`) classifies each
completed turn as `ns | cc | unknown` by payload shape (CC carries
`total_cost_usd`/`cc_session_id`; NS carries `debug`/`files`), and the browser
result records `route` and `cc_cost_usd` (`playwright/runner.py:229-232,
259, 331-332`). But `route` is used only to decide whether to build the NS debug
dict and whether to gate cost — **no `PassCriterion` asserts `route == "ns"` or
`route == "cc"`.** The signal exists in the pipeline and is thrown away for
verdict purposes.

**(d) "Off-topic / unrelated" is tested only as an NS-internal refusal, not as a
route.** The `unsupported` (7) + `writes_unsupported` (19) families assert
`parser_plan.mode == "unsupported"` — i.e. "NS could not turn this into a valid
NExtSEEK op" (`orchestrator.py:483-488`). That is NS declining, **not** the
top-level router deciding "this is not an NS query, hand it to CC (or refuse)."
There is no variant exercising "this should go to CC" or "this is unrelated to
the whole platform → refuse."

**Bottom line for the unification discussion:** the harness assumes NS mode was
already chosen and tests NS-internal dispatch. Adding router-level tests means
(i) adding a `route` field family to `criteria.py`, (ii) driving through the
top-level endpoint (the browser-tier / poll path already computes `route`), and
(iii) authoring variants that assert `route`. The plumbing to *detect* route
already exists in `poll.py`; only the *assertion* surface is missing.

---

## 4. Coverage of issues #32 / #33

### (a) #33 — REST-vs-graph routing consistency for the same intent: **ABSENT (worse: a contradiction is baked in).**

There is no variant that asserts two phrasings of one intent resolve to the
**same** engine. Instead the catalog encodes a **phrasing-dependent split** that
a consistency test would flag:

- `graph_query` family — `graph.which_monkeys_have_imaging_and`:
  `"Which monkeys have imaging and sequencing data"` → asserts
  `parser_plan.mode == "graph_query"`.
- `search_parents_by_child` family — `pbct.find_me_monkeys_that_have_imag`:
  `"Find me monkeys that have imaging and sequencing data associated with it"` →
  asserts `parser_plan.mode == "new_search"` + `api_plan.endpoint contains
  "parents_by_child_types"`.

These are the **same "monkeys with imaging AND sequencing data" intent**, yet the
catalog declares contradictory "correct" modes based purely on surface wording
("Which … have" vs "Find me … that have … associated with it"). The same pattern
recurs: `graph.what_studies_have_nhp_samples` ("… NHP samples with flow and
sequencing data" → graph) vs `pbct.find_monkeys_that_have_both_fl` ("… both flow
cytometry and sequencing data" → REST). The suite will happily pass both, so it
actively **encodes** the #33 inconsistency rather than catching it. There is no
"consistency" or "same-intent" assertion primitive at all.

### (b) #32a — child→parent traversal + parent-attribute aggregation: **PARTIAL (routing only, no aggregation).**

The `search_parents_by_child` family (18 variants) covers child→parent traversal
at the **routing** level. Every variant asserts:

```
- parser_plan.mode eq new_search
- api_plan.endpoint contains parents_by_child_types
- entity_sampletype_codes contains <NHP|MUS|TIS|D.SEQ>   (where applicable)
- api_ok true
```

(e.g. `pbct.mice_msp_rnaseq` "Which mice have both mass spectrometry and RNA
sequencing data?"). But **no criterion inspects the returned parent samples'
attributes** — nothing asserts that parent attributes were aggregated,
projected, or de-duplicated. The DSL *could* reach result files via
`api_artifact.*`, but no `parents_by_child` variant uses it. So traversal
**routing** is covered; parent-attribute **aggregation** is not.

### (c) #32b — recalled-bundle richness / "ask about last results" attribute follow-ups: **PARTIAL (weak reply-substring only).**

The `refine_and_recall` family (47 variants, 35 `ask_about_last_results`
assertions) is the relevant home. Attribute follow-ups exist and are asserted on
the **reply text**, not the bundle:

- `refrec.memory_unique_types`: recall turn `"What unique sample types are in
  those results?"` → `last_reply mentions "TIS"`.
- `refrec.memory_scientists`: `"What scientists are listed on those samples?"` →
  `last_reply mentions "Scientist"`.
- `refrec.memory_how_many`: `"How many results came back…?"` →
  `last_reply matches_re "\b\d+\b"`.
- `refrec.chained_filter`: 3-turn — count → `"give me the first 3 UIDs"` →
  `chat_log.length gte 3` + `last_reply matches_re "D\.SEQ"`.

These prove the reply *surfaces* an attribute value (which implies the recalled
bundle carried it), but they are **case-insensitive substring/regex checks on
the final reply** — brittle and indirect. Nothing asserts on the **bundle's
structural richness** (columns present, per-row attributes retained,
completeness). A `results_history.length` resolver exists (`criteria.py:168-169`)
and `chat_log.latest_bundle_id` (`:158`), but no variant asserts bundle contents.
So #32b is touched but not structurally verified.

### (d) Graph `LIMIT 250` cap: **ABSENT.**

No variant references a row cap. `graph_cypher` is asserted only `nonempty` (49×)
or `contains '<project token>'` (10×) — never parsed for a `LIMIT` clause, and no
criterion checks a returned row count against 250 (grep for `LIMIT`/`250` in
`catalog.json`: the only `limit` hit is the English word in a refine query
`"limit those to liver tissue"`). The cap is entirely untested here.

---

## 5. Strengths and gaps

### Strengths (why #31 calls it "strong and maintained")

- **Clean, validated input→output contract.** Every entry is a typed
  `Turn.query` → `list[PassCriterion]` pair, Pydantic-validated on load
  (`catalog.py:42-45`); unknown ops are rejected at parse time
  (`test_e2e_catalog.py:20-22`). Easy to read, diff, and extend.
- **Broad, real coverage.** 366 variants over 11 families exercise every active
  NS agent end-to-end against a live stack, with 358 explicit mode assertions
  across all 7 NS modes — the *inner* router is genuinely well covered.
- **Deterministic, budget-aware sampling.** Seeded per-family sampling with a
  `max(1, round(N*ratio))` floor guarantees ≥1 variant/family per run
  (`sampler.py:15-29`); `--seed` reproducibility recorded in the manifest;
  `--pace` throttles LLM cost; `--rerun [--failed-only]` replays a prior manifest
  (`manifest.py:38-48`).
- **Rich artifacts + reporting.** Per-turn `query.txt`/`reply.txt`/`debug.json`
  + captured orchestrator run-root (`runner.py:132-137`), plus `manifest.json`
  and a family-grouped `report.html` (`report.py`).
- **Two-tier design already spans process and browser.** The browser tier drives
  the real UI through the real endpoint via stable `data-testid` selectors
  (`pages.py`), and already computes the NS-vs-CC `route` and CC cost
  (`poll.py`) — the substrate for router assertions is present.
- **Well-tested harness, actively maintained.** ~18 `test_e2e_*.py` unit tests
  cover the runner, criteria DSL, sampler, manifest, report, catalog loader, and
  the playwright poll/trio/pages layers (all with stubbed `run_query`, so they
  need no stack). Recent additions show maintenance: natural-phrasing variants
  (`pbct.find_me_*`, `graph.how_many_*`), `routing.*` disambiguation variants,
  and the 2026-07-13 HTTP-polling rework of the browser transport
  (`playwright/runner.py:6`).

### Gaps / weaknesses

- **No top-level route assertion** (§3) — the primary gap for the unification
  work. The engine detects `ns|cc` but no criterion checks it, and no CC mode
  exists in the checked schema.
- **`trio_match` is a no-op in the CLI tier.** The CLI `check_pass` call passes
  no `browser_ctx`/`console_text`/`mysql_chat_log` (`runner.py:139-145`), so for
  op `trio_match` all three normalized strings are `""` and it trivially passes
  (`criteria.py:283-297`, `trio.py:38`). The one CLI variant carrying a `trio`
  criterion (`refrec.refine_to_cd8`) asserts nothing there — it only bites in the
  browser tier.
- **Assertions are structural, not correctness.** `api_ok=true` /
  `neo4j_ok=true` / `graph_cypher nonempty` verify a call *happened and shaped
  right*, not that the *right data* returned. There are no golden expected
  counts/rows, no LLM-judge, and reply checks are weak substring/regex. A parser
  that routes correctly but returns wrong rows passes.
- **Encodes the #33 contradiction** (§4a): phrasing-dependent graph-vs-REST
  "correct" answers with no consistency guard.
- **Heavy, slow, flaky infra dependence.** Needs a full seeded stack + Neo4j +
  real multi-provider LLM budget; default 15 s inter-turn pacing makes full runs
  long; LLM nondeterminism can flip mode assertions. Not runnable on a bare
  instance or in plain CI.
- **Thin browser tier.** Only 4 variants tagged; the richest cross-surface check
  (trio, MySQL, route, cost) rides on those 4 and is silently skipped when the UI
  is unreachable (`runner.py:272-279`).
- **Doc drift.** README/CLAUDE.md say "362 variants"; the catalog has **366**
  (`search_advanced` 66→68, `graph_query` 59→60). The per-family table in
  `README.md:121-133` is stale.

---

## 6. How inputs↔outputs are paired (reusability for a unified, router-aware harness)

**The pairing is clean and directly reusable.** The atomic unit is
`Turn{query} → list[PassCriterion{field, op, value}]` (`catalog.py:17-20`),
grouped into multi-turn `Variant`s that share a session. This decouples three
concerns that a unified harness wants separate:

1. **Stimulus** — `query` (+ multi-turn ordering).
2. **Expectation** — a declarative field/op/value list.
3. **Resolution** — a pluggable field-resolver keyed by a `field` prefix
   (`criteria.py:55-211`), already spanning debug dict, session state, filesystem
   artifacts, browser DOM, and MySQL rows.

That resolver-by-prefix design is the reuse lever: **a router-aware harness can
be added without touching the catalog schema or the op set** — just:

- add a `route` (and/or `top_level_mode`) field family to `resolve_field`,
  reading the `route` that `poll.py:117-129` already computes;
- author catalog variants that assert `{field:"route", op:"eq", value:"ns"|"cc"}`
  alongside (or instead of) the existing `parser_plan.mode` assertions;
- optionally add a same-intent **consistency** primitive (e.g. a variant with
  two paraphrase turns asserting identical `route`/`mode`) to close #33.

The manifest already carries `route`/`cc_cost_usd` in the browser path
(`playwright/runner.py:331-332`), and `ManifestEntry` (`manifest.py:11-17`) is a
small Pydantic model easily extended with a `route` field. Because both tiers
share the same `check_pass` DSL, one catalog could drive **NS in-process, NS via
the endpoint, and CC via the endpoint** — the missing piece is an entry point
that calls the top-level `cc-assistant/query/async` router (not `run_query`
directly) for the CLI/programmatic path, so that `route` is observable without a
browser. The CC harness could then contribute its own field families
(`cc.tool_calls`, `cc.cost`, `cc.files`) under the same catalog structure.

**Verdict:** this harness is an excellent base to extend into a unified,
router-aware suite. Its one structural blind spot — the top-level NS-vs-CC route
— is not a rewrite but an additive gap: a new `route` field family, a handful of
router-asserting variants, and a non-browser path through the top-level router.

---

### Key file references

- `e2e.py:30-133` — CLI front door / arg parsing / subcommand dispatch.
- `e2e/runner.py:79-163` — `run_variant` (per-turn `run_query` + `check_pass`);
  `:169-297` — `run_main` (plan → CLI tier → playwright tier → manifest/report);
  `:13,113` — the in-process `run_query` entry point; `:247,252` — playwright tag
  filter + UI-reachability gate.
- `e2e/catalog.py:11-45` — catalog Pydantic models + op enum.
- `e2e/criteria.py:55-211` — `resolve_field` (all field families);
  `:214-251` — ops; `:254-312` — `check_pass` (+`trio_match` special-case).
- `e2e/sampler.py:10-29` — env-gated per-family seeded sampling.
- `e2e/manifest.py:11-48` — manifest models + rerun filter.
- `e2e/playwright/poll.py:24-42,117-129,143-175` — endpoint transport + NS/CC
  route detection + NS debug reconstruction.
- `e2e/playwright/runner.py:100-333` — browser tier; `:331-332` — route/cost in result.
- `e2e/playwright/trio.py:22-59` — UI≡console≡MySQL trio; `pages.py` — testid page object.
- `src/chat_nextseek/orchestrator.py:382-483` — `run_query` NS pipeline + dispatch.
- `src/chat_nextseek/schemas/router.py:27-45` — `ParserPlan` (7-mode enum, no CC).
- `README.md:105-186`, `CLAUDE.md` (Testing) — how it's meant to be run.
