# Nessie hardening: routing continuity, provider resilience, harness truthfulness, and the write boundary

**Date:** 2026-08-03
**Branch:** `dev-v3-merge`
**Status:** design, awaiting review
**Sources:** `nessie-plain-english.md`, `nessie-orientation.md`,
`reports-testing-nessie/nessie-cc-state-2026-08-03.html`, the seed-6 run
(`/home/cdemu/nessie-run-seed6b/`) and its operator notes
(`nessie-notes (5).json`), plus the 2026-08-03 handoff.

---

## 1. What this spec is

Thirteen fixes drawn from four review documents and one live run, reduced to a
single sequenced work list. The organising principle is that **most of what the
seed-6 run reported as failure was not failure**: of eighteen reds, ten were one
infrastructure outage, three were a known harness defect, three were the corpus
disagreeing with a product that was right, and one was a real defect. Fixing the
instrument comes before reading it again.

Seven tracks, A through G. A and B are prerequisites for any further measurement.

---

## 2. Method, and what "verified" means

Every claim below carries a `file:line` and was checked against the tree at
`6e914a9` during this session, by reading the code or by running the real corpus
loader. Where a source document was wrong, the correction is recorded in §3
rather than silently dropped, because two of these errors have already survived
one handoff cycle.

Claims that are **not** verified are marked inline. There are three.

---

## 3. Corrections to the source documents

Do not build on these. All four were asserted in the source material and are
false at HEAD.

| Claim | Source | Reality |
|---|---|---|
| `writes_unsupported` has 19 variants, none a delete | orientation §6 | **2 active variants, and neither is a write.** ~17 `write.*` variants were retired on 2026-08-03. See D2. |
| Both `probe-2026-07-29*.json` raise `ValueError` on load | orientation §Operational state | All three probe files load cleanly through `corpus.load_case_file`. |
| `green.global_count` asserts `container_cc` | cc-state next-step 3 | It asserts `nextseek_query`. Its `route_policy` override is inert. See D1. |
| The two `advanced.find_mice_treated_with_50mg_nd*` cases assert `container_cc` | cc-state next-step 3, 08-03 handoff ANN-12 | They assert `nextseek_query` already, via the `search_advanced` family default. Nothing to fix. |

The mechanism behind the last two is worth stating once, because it will recur:
`corpus.apply_route_policy` appends a route criterion only `if "route" not in
present` (`nessie_tests/corpus.py:191`). A variant carrying an **inline** route
criterion therefore ignores its `route_policy` override entirely. Verified by
resolving all 280 active variants through `corpus.merged(overlay_path)`.

---

## 4. Track A — routing continuity

### A1. Sticky container_cc

**Problem.** The router classifies each turn independently, so a conversation
that starts in CC can be pulled back to NS mid-thread. Observed in seed 6:
`green.refine_recall`'s seed *"Find samples from a 4 week study."* routed
`container_cc` and produced the best answer in the run; its follow-up *"Just the
4 week ones."* routed `nextseek_query`, then failed `api_ok` because no NS bundle
existed to refine. The conversation broke in the middle.

**Evidence.** `/home/cdemu/nessie-run-seed6b/manifest.json`, case
`green.refine_recall`: seed observed `container_cc`, case-level `route:
nextseek_query`.

**Change.** In `_decide_route` (`nextseek_api/services/cc_assistant.py:203`),
after the router call and **after** the existing `pipeline_agent` check:

```python
if decision.route == cc_router.ROUTE_NS and _prev_route_was_cc(history):
    return cc_router.RouteDecision(
        route=cc_router.ROUTE_CC, model_class="opus",
        model_id=cc_router._resolve_cc_model_id(),
        reasoning=f"sticky_cc; router said ns ({decision.reasoning})",
        source="sticky")
```

`_prev_route_was_cc(history)` is `bool(history) and history[-1].router_choice ==
"container_cc" and history[-1].status == "completed"`. A CC turn that errored must
not trap the chat.

`history` already reaches `_decide_route` (`cc_assistant.py:342`), carrying
`router_choice` per turn from `router_context.build_history`. No new state, no
migration, no BAML change.

**Resulting precedence:** `force_route` (admin) > `pipeline_agent` > sticky > router.

**Accepted consequence, stated explicitly.** Because the guard fires on every
subsequent turn, a chat that reaches CC stays in CC until the user starts a new
chat or an admin forces a route. A simpler predicate-based variant was designed
and rejected as over-complex; the operator chose this deliberately. The escape
hatches are `force_new` and `force_route`.

**Prior art, and the reason for the ordering.** The same feature exists for the
nf-core wizard and its first implementation was a bug: it short-circuited
*before* the router, so an open build captured every following turn and a plain
sample search was answered by the samplesheet builder
(`cc_assistant.py:245-252`). The fix was to let the router decide first. This
guard sits after the router for the same reason, and after the pipeline check so
the wizard keeps winning.

**Acceptance.**
- Unit: prior turn CC + router NS → CC with `source == "sticky"`.
- Unit: prior turn CC but `status == "error"` → router's decision stands.
- Unit: empty history → router's decision stands.
- Unit: `force_route` beats sticky; `pipeline_active` beats sticky.
- No corpus case changes verdict. `apply_route_policy` only attaches a route
  criterion to `turns[0]`, which is always cold, so nothing existing asserts a
  route on a follow-up.

---

## 5. Track B — provider resilience

### B1. The Bedrock 503 fallback chain is unreachable

**Problem.** Ten of eighteen seed-6 reds were one Bedrock outage:
`All provider fallbacks exhausted — agent 'parser': ServiceUnavailableException
(reached max retries: 4)`. The fallback machinery exists and does not fire for
Bedrock.

**Root cause, verified.** `_FALLBACK_CHAINS`
(`chat_nextseek/src/chat_nextseek/schemas/schema_helper.py:26-33`) is keyed on the
**catalog** provider vocabulary:

```python
("default", "gcp"):  [...],
("default", "anth"): [...],
```

but the lookup uses the **client** vocabulary:

```python
failed_provider = getattr(target_client, "provider", None)   # :256
_FALLBACK_CHAINS.get((catalog_key, failed_provider), [])     # :47
```

Client attributes are `openai` (`llm_clients.py:89`), `gcp` (`:173`),
`anthropic` (`:291`), `bedrock` (`:440`). Only `gcp` coincides with a chain key.
A 503 from `BedrockClient` looks up `("default", "bedrock")`, gets `[]`, and
raises `LLMFatalError`.

`agent_model_catalog.json`'s `default` profile routes `parser`, `report_writer`,
`report_coder` and `multi_parser` to `us.anthropic.claude-opus-4-7` with
`provider: "anth"`, resolved to `BedrockClient`. So the fallback is dead
precisely on the agents whose failure kills the whole turn. `config.py:1100`
documents the split without noticing it: *"Keys mirror the catalog 'provider'
field: 'gcp', 'anth', 'oai'."*

**Change.** Normalise at the lookup site rather than renaming either vocabulary,
because both are load-bearing elsewhere (`LLM_CLIENTS` is keyed by catalog
vocabulary at `config.py:1094`; `LLMError.provider` is reported to users).

Add to `schema_helper.py`:

```python
_CLIENT_TO_CATALOG_PROVIDER = {
    "bedrock": "anth", "anthropic": "anth", "gcp": "gcp", "openai": "oai",
}

def _catalog_provider(client) -> str:
    raw = getattr(client, "provider", None) or ""
    return _CLIENT_TO_CATALOG_PROVIDER.get(raw, raw)
```

and use it at `:256`. Add `("default", "oai")` and the `oai` chains only if an
OpenAI-primary deployment is in scope; otherwise leave OpenAI unmapped and let
it behave as today.

**Acceptance.**
- Unit: a stubbed `BedrockClient` raising `LLMServiceUnavailableError` for agent
  `parser` under `catalog_key="default"` yields a non-empty fallback list whose
  first entry is a GCP client.
- Unit: `_catalog_provider` maps all four client classes correctly.
- Unit: the existing GCP fallback path is unchanged.
- Regression: a table test asserting every distinct `provider` class attribute in
  `llm_clients.py` has a mapping, so a new client cannot silently reintroduce this.

### B2. The harness cannot tell an outage from a failure

**Problem.** The nine (ten, see below) outage turns were scored as ordinary
`failed`. Every outage therefore reads as a regression and half a run's signal is
lost. The 2026-08-03 triage attributed nine cases; the correct count is ten.

**Evidence.** `cons.nhp_sequencing_engine` was triaged `drift` on the strength of
its own message, *"count could not be resolved for 2 of 2 queries"*. Its two
turns (ids 1054, 1055 in `turns.json`) both returned
`All provider fallbacks exhausted — agent 'parser'`. The operator's note on that
case reads simply "this is provider error", and was right. Eighteen turns in the
run carry the marker.

**Change.**
1. In `nessie_tests/evaluate.py`, detect a reply matching
   `All provider fallbacks exhausted` (or an `LLMFatalError` signature) and mark
   the turn `error`, not `failed`.
2. `nessie_tests/runner.py` must exclude `error` turns from the pass/fail
   headline and report them on their own line.
3. **The consistency group must do the same.** `nessie_tests/consistency.py`
   replaces the turn replies with its own summary message, which is exactly why
   this case was mis-triaged by a careful reviewer. The outage check has to run
   before the count-resolution message is composed.

**Acceptance.**
- Unit: a manifest turn whose reply carries the marker is classified `error`.
- Unit: a consistency group whose member turns carry the marker reports `error`,
  not a count-resolution failure.
- Regression against the stored seed-6 evidence: ten cases classify `error`, and
  the headline becomes `39 pass / 10 error / 8 fail`.

---

## 6. Track C — harness truthfulness

### C1. The family floor keys off family, not engine

**Problem.** `corpus.apply_family_floor` attaches a floor based on `v.family`,
which is engine-shaped (`search_advanced` implies REST), but the parser may
legitimately answer with a different engine. A `search_advanced` case answered by
`graph_query` can never satisfy `api_ok` / `api_outcome_observed` however correct
the answer is. Flagged 2026-07-29, still open, three false reds in seed 6.

**Evidence.** `advanced.find_me_sequencing_files_assoc` (1,765),
`advanced.find_me_nhp_samples_from_study_2` (408),
`advanced.find_me_d_seq_samples_in_proje` (1,858) all routed NS correctly and
answered via graph. The operator's notes on all three: *"this was correct"*,
*"also correct"*, *"also correct"*. Their round-2 notes predicted two verbatim.

The same defect makes any NS floor on a **CC** case an automatic red, since
`api_outcome_observed`, `graph_outcome_observed` and `report_produced_output` are
constant-false on a CC turn.

**Constraint that shapes the fix.** `apply_family_floor`
(`nessie_tests/corpus.py:45`) runs inside `corpus.merged()` at **corpus-build
time** and appends static `PassCriterion` objects. It cannot know which engine
ran. So "pick the floor from the observed engine" is not implementable there, and
the fix has to split across build time and evaluation time.

The three floor fields are already **derived at evaluation time**
(`evaluate.py:117-119`) from the debug payload, which is the seam to use.

**Change, in two parts.**

1. **Build time: make the assertion engine-agnostic.** Add a derived field
   `outcome_observed = api_outcome_observed or graph_outcome_observed or
   report_produced_output`, computed alongside the existing three in
   `build_observed_debug`. Point the floors for engine-flexible families
   (`search_advanced`, `search_retrieve`, `search_parents_by_child`, `search_tree`,
   `graph_query`) at `outcome_observed` instead of the engine-specific field. A
   `search_advanced` case answered by graph then satisfies its floor, and a case
   that answered with nothing still fails.

   Keep the engine-specific fields available so a case that genuinely must use one
   engine can still assert it by hand. The floor stops mandating an engine; it does
   not stop anyone asserting one deliberately.

2. **Evaluation time: do not apply an NS floor to a CC turn.** Extend the existing
   unobservable-criteria skip (`evaluate.py:282`, which already records criteria as
   `skipped` rather than failing) so that on a turn whose observed route is
   `container_cc`, the NS outcome fields are skipped rather than evaluated as
   constant-false. Without this, part 1 alone still auto-reds every CC-routed case
   in a floored family, since all three inputs to `outcome_observed` are false on a
   CC turn.

**Acceptance.**
- Unit: `outcome_observed` is true when any one of the three inputs is true, and
  false when none is.
- Unit: a `search_advanced` variant answered by `graph_query` satisfies the floor.
- Unit: a CC-routed turn records the NS outcome criteria as `skipped`, not `failed`.
- Unit: a turn that produced no outcome at all still fails the floor. This is the
  regression that matters, because part 1 weakens the assertion and must not
  weaken it into vacuity.
- Regression against stored seed-6 evidence: the three `advanced.*` cases pass
  without editing the cases themselves.

### C2. `evaluate.py` cannot see a CC artifact

**Problem.** No CC case can prove a file exists. `build_artifact_index`
(`nessie_tests/evaluate.py:60-76`) reads `debug["report_saved_files"]` and
`query_complete["files"]`. `cc_engine.py:789-790` emits
`query_complete["artifacts"]` and `query_complete["cc_raw_files"]`. Different
keys, so `api_artifact.*` resolves false on every CC turn, permanently.

cc-state calls this the highest-value harness change available and states that
fixing it comes before running anything, because it gates
`export_and_file_delivery`, `batch_upload_preparation` and
`pipeline_output_reingest` — the three families that produce files, and the three
that have never been routed to.

**Change.** In `build_artifact_index`, additionally collect from
`query_complete["artifacts"]` (each entry's `label` / `path`) and
`query_complete["cc_raw_files"]`. One function; the criterion vocabulary is
unchanged.

**Acceptance.**
- Unit: a synthetic `query_complete` carrying `artifacts` produces a populated index.
- Unit: the existing `report_saved_files` and `files` sources still work.
- Blocks F2.

### C3. A route_gate case is not free, and the code comment says it is

**Problem.** `nessie_tests/runner.py:126-128` states route_gate cases *"never
execute a real turn/launch, even in a full run"*. They do.
`services/cc_assistant.py:560` starts the turn on a daemon thread and returns
202; the only early return is `ROUTE_UNRELATED` (`:352-366`); both the NS and CC
branches fall through into full execution. `http_driver.py:96-98` breaks the
**client** poll loop only, and there is no cancel, abort or DELETE anywhere. Cost
is read off `query_complete` (`runner.py:151-153`), which route-tier polling never
observes, so the run prints `$0`.

**Change.** Correct the comment, and either record route-tier cost from the task
row after the fact or print `cost: unmeasured` rather than `$0`. Do not print a
number the harness cannot observe.

**Acceptance.** The comment matches the code; a route-tier run does not report `$0`.

---

## 7. Track D — corpus correctness

### D1. Settle the remaining route assertions

Three edits, not the six the source documents list.

| Case | Change | Why |
|---|---|---|
| `green.refine_recall` | flip the **inline** `route eq nextseek_query` at `overlay.json:65` to `container_cc` | The router is right: it cited `ambiguous_study_resolution` and returned 15 keyword matches, flagged most as substring artifacts, and separated the 2 genuine `Cohort='4 week'` samples. That matches ground truth exactly. Its `route_policy` override is already `container_cc` and inert. |
| `sys.what_sample_types_are_most_com` | fix `route_capabilities.json`, not the case | Operator ruling: *"this should be nextseek query system question"*. The router sent it to CC citing `open_ended_analysis`; the capability text needs to stop claiming group-by-and-rank over the catalog. |
| `sys.who_is_the_current_user` | keep the corpus assertion (`nextseek_query`); fix `route_capabilities.json` | Operator ruling: NS. The router sent it to CC for shell access and then answered from context anyway, offering to read `NEXTSEEK_USERNAME` rather than reading it, so CC buys nothing here. |

**Not needed:** `green.global_count` and both water-study cases. See §3.

**Also in this track:** `graph.what_investigations_exist_in_t` needs one word from
the operator. The triage called it a stale assertion (REST answered it correctly);
the note reads *"this is also correct but should probably be graph query yes"*,
which could mean either. Listed as an open decision, §11.

### D2. Restore write and delete refusal coverage

**Problem, and it is ours.** The 2026-08-03 retirement pass removed roughly
seventeen `write.*` variants as near-duplicates. `writes_unsupported` is now
**2 active variants** — `write.download_all_samples_from_the` and
`write.export_all_metadata_for_nhp_22` — and **neither is a write**. There is no
longer any test that the assistant refuses to create, update or delete, and there
never was one for delete.

This matters more given Track E: E1 is a real mutation path, and nothing in the
corpus would notice it opening.

**Change.** Author two or three cases, not fifteen. One create-confirmation case,
one update case, one delete case, each asserting the agent proposes and asks
rather than acts, with a negative guard on any reply claiming the mutation
happened. Reinstate from `retired.json` where a retired variant already has the
right shape; retirement is reversible by design.

**Acceptance.** A delete-intent question exists in the corpus and fails if the
assistant reports having deleted anything.

---

## 8. Track E — the write and identity boundary

This track is a genuine security change, included at the operator's explicit
request. It is sequenced last among the code tracks because it touches auth and
needs its own review.

### E1. The NS REST path can issue DELETE

**Problem, verified end to end.** Write safety is asymmetric. Neo4j is
hard-blocked by a regex before the driver opens
(`chat_nextseek/.../helpers/tools/neo4j.py:72-78`). REST is not:

1. `prompts/parser_core_routing.txt:77` explicitly permits *"Updating or patching
   a specific sample record by UID"*, and nothing in the `unsupported` rules
   forbids write intent.
2. `context/min_api_endpoints_enriched.json` advertises exactly one DELETE:
   `/nextseek_api/samples/{uid}/`, described *"Permanently delete a sample by
   UID"*, with `intent_patterns: ['delete', 'remove']`.
3. `agents/api.py:217-222` resets the method to the default **only when it is not
   in the endpoint's allowed list**. For that endpoint, DELETE *is* the allowed
   method, so the guard sanctions it.
4. `helpers/tools/nextseek_api.py:54` issues `requests.request(method=method, …)`
   with no method filter.
5. The write gate that exists (`nextseek_api/assistant/write_gate.py`) is
   imported only by `nextseek_api/assistant/granular.py:24`, which serves the
   *other* ViewSet. It is not on the `run_query` path.

The only remaining barrier is SEEK per-object authorisation on whatever
credentials the turn carries, which E2 shows is weaker than it looks.

**Design principle.** The corpus already encodes the intended policy: writes route
to `container_cc`, where `nextseek-api-write` exits `WRITE_BLOCKED` without an
explicit `--confirmed-write` and the skill requires a plain-text confirmation
first. So **the NS REST tool should be read-only, full stop**, mirroring Neo4j.

**Change.** Deny by default at the single chokepoint,
`tool_nextseek_api_request` in `helpers/tools/nextseek_api.py`: refuse any method
outside a read allowlist (`GET`, plus `POST` for the search endpoints that use a
request body) before the call is made, raising the same shape of error the Neo4j
guard raises. Then, as defence in depth and prompt hygiene:

- remove the DELETE entry from `min_api_endpoints_enriched.json` so the agent
  cannot select what it cannot see;
- narrow `parser_core_routing.txt:77` so update intent routes to `unsupported`
  rather than to the REST corridor.

The chokepoint is the boundary; the other two reduce the number of turns that
reach it and fail.

**Acceptance.**
- Unit: `tool_nextseek_api_request` raises on `DELETE`, `PATCH` and `PUT` and
  makes no network call.
- Unit: search `POST` still works.
- Corpus: the D2 delete case routes away from the REST corridor.
- **Not verified and must be checked during implementation:** whether any
  legitimate NS flow (reporter, submission generation, pipeline) issues a
  non-search `POST`. Grep before enforcing.

### E2. Identity is not a data boundary

**Problem, verified.** Three separate holes.

1. **Every synced SEEK user is staff.** `dmac/views.py:80` and `:97` set
   `user.is_staff = 1` on sync. `nextseek_api/views.py:603` then does
   `is_superuser = bool(request.user.is_superuser or request.user.is_staff)`,
   with the comment *"Treat Django staff as admin for data scope, matching
   IsAdminUser"*, and takes the unfiltered branch. Project membership therefore
   does not restrict what the assistant returns.
2. **Silent service-account fallback.** `orchestrator.py:427-432` overrides
   `config.API_USER` / `API_PASS` only `if credentials` and only when each key is
   truthy. With absent session credentials the turn proceeds as whatever
   `ChatConfig` was built with, which in the shipped template is the
   `demo`/`demopassword` service account. The turn does not fail and does not warn.
3. **WebSocket ownership is conditional.** `nextseek_api/assistant/consumers.py:97`
   reads `if user and user.is_authenticated and task.user_id != user.pk: return
   None`. With no authenticated user the ownership test is skipped and the
   connection is accepted, delivering the full progress stream and the final
   answer to anyone holding the task UUID. The HTTP fallback for the same data
   (`services/assistant.py:924-938`) enforces both.

**Change.**

- **(3) first, it is the cheapest and the most clearly wrong.** Require an
  authenticated user *and* ownership. The harness is unaffected: `http_driver`
  polls over HTTP, and the browser carries the session cookie. Leave
  `_is_allowed_origin`'s `None → True` alone; it exists for non-browser clients
  and is not the hole once auth is enforced.
- **(2)** Make the fallback explicit: when session credentials are absent, log a
  warning naming the account being used, and gate the silent substitution behind
  a setting that defaults to off in any non-demo deployment.
- **(1)** Stop conflating staff with superuser for **data scope** at
  `nextseek_api/views.py:603` — use `is_superuser` alone there. Do not change
  `is_staff` at sync time: it is likely load-bearing for Django admin access and
  changing it has a blast radius outside the assistant.

**Acceptance.**
- Unit: an unauthenticated WS connection with a valid `task_id` is rejected.
- Unit: an authenticated non-owner is rejected.
- Unit: a turn with no session credentials logs the substitution.
- Integration: a non-superuser staff user's retrieve is project-scoped.
- **Not verified and must be established before shipping (1):** which callers
  depend on the staff-as-admin behaviour today. This is the highest-risk change
  in the spec and should ship on its own commit with its own review.

---

## 9. Track F — unblocking the CC surface

### F1. Raise the CC turn timeout

`pipeline_output_reingest` has never completed a turn: killed by the $0.50 budget
cap, then by 180s, then by 300s. The cap is **already env-configurable** —
`_TIMEOUT_HARD_MAX = int(os.environ.get("NEXTSEEK_CC_TIMEOUT_HARD_MAX", "180"))`
(`nextseek_api/cc_assistant/cc_engine.py:76`) — and `docker/nextseek.env` sets no
override. So this is a config line, not a code change.

**Caveat that changes the work.** Raising the wall from 180s to 300s previously
bought nothing, which is the signature of work looping rather than work
marginally over budget. Raise the cap **and** capture a trace of one reingest turn
before concluding anything. If it loops, the timeout was never the bug.

### F2. Run the CC probe

`nessie_tests/probes/probe-cc-2026-07-31.json`, 13 cases / 16 turns / about $3 and
30-40 minutes. It is the only measurement of the CC side that exists, and no CC
turn has ever been captured end to end in a stored manifest.

**Blocked on C2**, per cc-state: until `evaluate.py` can see artifacts, the three
file-producing families can only prove that a reply mentioned a workbook.

After the run, read the observed `last_reply` out of the manifest **before**
tightening any regex. The probe's criteria were derived from code, not from an
observed run.

---

## 10. Track G — documentation drift

Low risk, no dependencies, batchable into one commit.

- Root `CLAUDE.md` does not mention Nessie at all, and its service table lists 7
  containers against 9 in `docker-compose.yml`.
- `chat_nextseek/CLAUDE.md:42` and `README.md:236-257` still document the retired
  Seqera Tower launch path; `pipeline/agent_tools.py:218` says verbatim that Tower
  is retired and Luria is the only exposed target. The sixth wizard tool,
  `handoff`, is undocumented.
- Three corpus counts circulate: `chat_nextseek/CLAUDE.md:22` and `README.md:119`
  say 362, `catalog.json` holds 366, older docs say 381/447. The live corpus is
  **280 active variants**.
- `nessie_tests/README.md` is stale on known-fail policy: the merged corpus now
  contains only `cons.nhp_sequencing_engine` with that tag.
- `nessie_tests/output-skill/SKILL.md`'s gotcha list is a 2026-07-24 snapshot: it
  claims there is no xpass detection (there is) and a 30s socket timeout (it is 120s).
- `UI.md:140` points at `/seek/salt/`; `seek/urls.py:13` defines only `^assistant/`.
- `dmac_assistant/README.md` overstates usage (`streamjson` is never imported) and
  describes host bind mounts `docker-compose.yml` no longer uses.
- Outer `CLAUDE.md`'s frontend row conflates `npm run build` (emits
  `chat_frontend/dist`, referenced by nothing) with `npm run build:embedded`
  (what the site actually serves).

---

## 11. Sequencing

```
B1 ─┬─> B2 ─┬─> C1 ─> [wide re-run is meaningful]
    │       │
    │       └─> C3
    │
A1 ─┘

C2 ─> F2                    (artifact keys before the CC probe)
F1 ─> F2                    (timeout before reingest can complete)

D1, D2, G                   independent, any time
E1 ─> E2(3) ─> E2(2) ─> E2(1)   own commits, own review
```

**B1 before any re-run.** Without it the next outage produces the same ten
unusable reds and the run is again half noise.

**C1 before any wide sample.** It is producing false reds now and will produce
more at a wider sample, and it obscures real failures.

**Nothing is compared to the 07-28 or 07-29 runs.** Seed 6 shares only 16 of 56
variants with seed 0, the corpus has since lost 101 variants and gained 280 route
assertions, and `route_capabilities.json` changed live. Comparisons across that
boundary are invalid.

---

## 12. Acceptance for the whole spec

1. The harness unit suite is green, plus the new tests named above. The
   2026-08-03 handoff records 160 passing at `04a20bf`; that number was not
   re-verified for this spec, because the suite needs the container
   (`mysqlclient` does not build on the host). Run it in the container and record
   the real baseline before starting.
2. Replayed against the **stored** seed-6 evidence, with no paid re-run: ten cases
   classify `error`, and the three `advanced.*` cases pass unedited.
3. A fresh seed-6 re-run reproduces no provider-exhaustion failures, or reports
   them as `error` if the provider is genuinely down.
4. The CC probe has run once and its manifest is stored, with at least one case
   proving a file exists rather than proving a reply mentioned one.
5. A delete-intent corpus case exists and fails if the assistant claims to have
   deleted anything.
6. `tool_nextseek_api_request` refuses `DELETE` / `PATCH` / `PUT` in a unit test.
7. An unauthenticated WebSocket connection holding a valid `task_id` is rejected.

---

## 13. Open decisions

1. **`graph.what_investigations_exist_in_t`.** The note *"this is also correct but
   should probably be graph query yes"* reads two ways: keep the graph expectation
   and treat REST-answering as a product issue, or accept REST and retire the
   assertion. One word settles it.
2. **`4 week` totals.** `Cohort='4 week'` is 2 and `'4wk'` is 237, plus `4wk_Day1`
   8 and `4wk_Day2` 8, so the answer is 239 or 255 depending on whether day-level
   cohorts count. Blocks tightening `cc.ambiguous_four_week`.
3. **Histograms.** The CC image ships polars, xlsxwriter and orjson, and not
   matplotlib, scipy, seaborn or pandas, so *"make a histogram"* can only be
   answered as text today. Text answer, or add matplotlib? The criteria differ
   completely and this blocks `open_ended_analysis`.
4. **Exercising the write path on the dev box.** E1's tests are unit-level and
   safe. Whether any live confirmation-gate case may run against dev is a separate
   call, given the server completes a turn behind an abandoned poll.
5. **OpenAI fallback chains.** B1 leaves `oai` unmapped. Add chains only if an
   OpenAI-primary deployment is in scope.
