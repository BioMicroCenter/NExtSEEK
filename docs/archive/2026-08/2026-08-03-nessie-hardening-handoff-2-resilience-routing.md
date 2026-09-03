# Handoff 2 — Provider resilience & routing continuity

*Read this before `docs/archive/2026-08/2026-08-03-nessie-hardening-plan-2-resilience-routing.md`.
Every number and line reference below was verified on 2026-08-03 against `9b7954a`.*

---

## What you are picking up

Nessie is the AI assistant in NExtSEEK. It has **two engines**: NS, a deterministic
multi-agent pipeline (`chat_nextseek/`) that turns a question into REST calls, Cypher or
reports; and CC, a per-turn ephemeral Claude Code container for open-ended work. A BAML
router picks between them per turn, or declines as `unrelated`.

You own two product fixes on that seam:

**B1 is the important one.** The last live run lost ten of its eighteen failures to a
single Bedrock outage, and the reason the outage was fatal rather than survivable is a
two-line vocabulary mismatch. Fixing it is the difference between a provider blip costing
nothing and costing a whole test run.

**A1 is the one the operator asked for.** Conversations currently change engine
mid-thread.

You also own a documentation sweep, because those files are yours and nobody else's.

---

## Environment

**Repo:** `/home/cdemu/code/dmac/docker/dev-v3-merge`, branch `dev-v3-merge`. Cut your
worktree from it.

> **Three checkouts exist on this machine. Only one is yours.**
> - `/home/cdemu/code/dmac/docker/dev-v3-merge` — yours
> - `/home/cdemu/code/dmac/docker` — branch `feat/luria-launch-mode`, unrelated
> - `/home/cdemu/code/chat_nextseek` — the canonical chat_nextseek repo, branch `cd-dev`
>
> **The third one is a trap.** See the vendoring section below.

**A local stack is running** (`nextseek`, `seek`, `seek-mysql`, `neo4j`, and a Bedrock
proxy). You need it for the container test lane. Do **not** rebuild or restart it — the
operator drives Docker, and other sessions share the box.

### Test lanes, both verified

*Host lane*, for anything under `chat_nextseek/`:
```bash
cd chat_nextseek && uv run --no-project --with pytest --with pydantic --with requests \
  python -m pytest tests/<sel> -q -p no:cacheprovider
```
Plain `uv run pytest` fails: `mysqlclient` will not build on the host. The
`--no-project --with` form sidesteps the whole project dependency set.

*Container lane*, for anything under `nextseek_api/`:
```bash
docker cp nextseek_api/services/cc_assistant.py nextseek:/app/nextseek_api/services/cc_assistant.py
docker cp nextseek_api/cc_assistant nextseek:/app/nextseek_api/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/cc_assistant/tests/<sel> --no-migrations -q -p no:cacheprovider'
```

**Copy the source with the tests, every time.** Copying only the tests runs them against
the deployed image's older source. That produced a confusing failure during planning that
looked like a real regression and was pure version skew.

**Baseline: 29 passed, 1 failed.** The failure is real, pre-existing, and stale — see
task 1. After task 1 your baseline is 30 passed.

---

## The vendoring trap, stated once and plainly

`chat_nextseek/` inside this repo is a **vendored snapshot** of
`github.com:cdemurjian/chat_nextseek.git`. `startup/scripts/sync_chat_nextseek.sh` exists
to bump that snapshot from the canonical clone.

**Do not run it.** The canonical clone at `/home/cdemu/code/chat_nextseek` is on branch
`cd-dev` and differs from this snapshot in **213 files**. The snapshot here is anchored on
the `dev` line by a deliberate v3-merge decision recorded as "vendored snapshot, never
line-merge". Syncing would silently revert that merge.

**Edit the snapshot in place.** That is what every prior fix on this branch did — `2ef28d8`
(graph), `f5a13c9` (llm_clients), `1053303` (llm), `d89a415` (api) are all ordinary commits
touching `chat_nextseek/` directly. Your B1 change to `schema_helper.py` follows that
pattern.

---

## What is already established, so you do not re-derive it

**The B1 root cause is fully traced.** You do not need to rediscover it:

- `_FALLBACK_CHAINS` keys: `("default","gcp")`, `("default","anth")`, and four more, all
  using the **catalog** vocabulary (`schema_helper.py:26-33`)
- the lookup passes `getattr(target_client, "provider")` (`:256`), the **client** vocabulary
- client attributes: `openai` `:89`, `gcp` `:173`, `anthropic` `:291`, `bedrock` `:440`
  (`llm_clients.py`)
- only `gcp` overlaps, so GCP fails over and Bedrock never does
- `default` profile puts `parser` + `report_writer` on Bedrock Opus
  (`agent_model_catalog.json`), which are the two agents whose failure kills a turn
- `config.py:1100` documents the split without noticing it

**The A1 history plumbing already exists.** `router_context.build_history` projects the
last 5 `chat_log` entries into typed `HistoryTurn`s carrying `router_choice` and `status`;
`cc_assistant.py:342` builds it and passes it to `_decide_route`; `router.baml:81` already
renders each prior turn's route into the prompt. You are adding a deterministic guard, not
plumbing.

**The A1 design was argued and settled.** A predicate-based variant (stay CC unless the
query is clearly a catalog lookup) was designed and measured against 17 real CC follow-ups
and 160 corpus NS questions. The operator rejected it as over-complex and chose the plain
rule. Do not reintroduce it.

---

## Traps

**1. Guard order in `_decide_route` is load-bearing.** The comment at
`cc_assistant.py:245-252` records that the wizard's first stickiness implementation
short-circuited *before* the router and captured every following turn — a plain sample
search got answered by the samplesheet builder, replying "searching the database isn't
something I can do". The fix was to let the router decide first. Your guard goes after the
router **and** after the pipeline check, for the same reason.

**2. Only ever convert NS to CC.** If the guard touches `unrelated`, an out-of-scope
question in a CC conversation starts spinning up an Opus container instead of returning the
canned refusal. Test it.

**3. Do not stick to a failed turn.** If the previous CC turn errored, the chat must be
free again. Otherwise one bad turn traps the conversation in the engine that just failed.

**4. Task 4 is unverifiable locally.** `route_capabilities.json` is prompt context read by
the BAML router at runtime. Nothing you can run proves the routing changed. Say so in the
completion note rather than implying it is tested. The proof is the post-merge live run.

**5. Do not put a corpus question into `example_queries`.** Anything there becomes a
few-shot example, and a test over it measures literal recall rather than routing. Three
real questions have already been burned this way, one of them by an agent that derived the
example *from* the question it later wanted to test.

---

## Cross-plan constraints

Two other agents are working in parallel worktrees from the same branch. The split is by
file ownership so you should never conflict. Two seams matter anyway:

**Your A1 introduces a new `route_source` value: `"sticky"`.** Plan 1 owns the corpus and
has been told not to assert `route_source eq baml` on any follow-up turn. Nothing asserts
it today (verified across all 280 active variants). If you change the *name* of the new
source, say so loudly in your completion note, because plan 1's tests and the CC probe both
reference it.

**Plan 3 also edits `chat_nextseek/`, in different files.** They own
`helpers/tools/nextseek_api.py`, `context/min_api_endpoints_enriched.json`,
`prompts/parser_core_routing.txt` and `orchestrator.py`. You own `schemas/schema_helper.py`.
No overlap. If you find yourself wanting one of theirs, stop.

---

## Definition of done

1. Host lane green for `chat_nextseek/tests/`, including the new fallback tests.
2. Container lane **30 passed** plus your new sticky-routing tests, 0 failed.
3. A stubbed Bedrock client raising a 503 for `parser` yields a non-empty fallback list
   whose first entry is a GCP client.
4. The anti-drift test passes: every `provider` attribute in `llm_clients.py`, discovered
   by introspection, has a mapping.
5. The sticky guard converts NS to CC only, never `unrelated`, never after an errored turn,
   and loses to both `force_route` and `pipeline_active`.
6. `git diff --name-only dev-v3-merge...HEAD` shows nothing under `nessie_tests/` and none
   of plan 3's files.
7. **Do not merge. Do not push.** The orchestrating session reviews all three diffs together.

---

## If you get blocked

- **The local stack is down.** Do not start or rebuild it. Report it; the operator drives
  Docker on this box and other sessions share it.
- **A change wants a file you do not own.** Stop, note it, leave it. The file-ownership
  split is the only thing preventing three-way merge conflicts.
- **You think the sticky rule is wrong.** It has a known, accepted downside: a chat that
  reaches CC stays there until a new chat. The operator chose this over a smarter predicate
  after seeing both. Implement it as specified; put any concern in the completion note.
- **You disagree with the B1 root cause.** Test it before accepting it. Two of the four
  documents behind this work contained claims that were false at HEAD, and they were caught
  by someone checking rather than complying.

---

## Background reading, in order

1. `docs/archive/2026-08/2026-08-03-nessie-hardening-design.md` — §4 (A1), §5 (B1), §7 (D1), §10 (G)
2. `nextseek_api/services/cc_assistant.py:203-258` — `_decide_route` and the wizard
   precedent comment
3. `chat_nextseek/src/chat_nextseek/schemas/schema_helper.py:26-80,248-275` — the chain and
   the lookup
4. `/home/cdemu/nessie-seed6b-review.html` — the run, including the outage section
