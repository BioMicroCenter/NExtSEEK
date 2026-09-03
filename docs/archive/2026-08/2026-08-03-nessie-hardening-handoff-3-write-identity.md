# Handoff 3 — The write and identity boundary

*Read this before `docs/archive/2026-08/2026-08-03-nessie-hardening-plan-3-write-identity.md`.
Every line reference was verified on 2026-08-03 against `9b7954a`.*

---

## What you are picking up

Nessie is the AI assistant in NExtSEEK, a scientific data curation platform holding
**50,887 real sample records** across six investigations. It has two engines: NS, a
deterministic pipeline that turns questions into REST calls and Cypher; and CC, an
ephemeral container for open-ended work.

You own the only track in this hardening effort that changes a security boundary. Four
findings, all verified by reading the code, none of them theoretical:

1. **The NS REST path can issue `DELETE` on a sample.** Not "could in principle" — the
   whole chain is present: the prompt permits update intent, the catalog advertises the
   delete endpoint with `intent_patterns: ['delete','remove']`, the method guard sanctions
   it, and the HTTP call is unfiltered.
2. **Project membership is not a data boundary.** Every synced user is staff, and staff is
   treated as superuser for data scope.
3. **The progress WebSocket accepts unauthenticated connections** that hold a task UUID,
   and hands them the full stream and the final answer.
4. **Credentials silently fall back to a shared service account** when a session has none.

Nothing here has been exploited and none of it is on a public network. It is worth doing
carefully rather than fast.

---

## Environment

**Repo:** `/home/cdemu/code/dmac/docker/dev-v3-merge`, branch `dev-v3-merge`.

> **Three checkouts exist. Only one is yours.**
> - `/home/cdemu/code/dmac/docker/dev-v3-merge` — yours
> - `/home/cdemu/code/dmac/docker` — branch `feat/luria-launch-mode`, unrelated
> - `/home/cdemu/code/chat_nextseek` — canonical chat_nextseek, branch `cd-dev`. **A trap.**

**`chat_nextseek/` is a vendored snapshot. Edit it in place.** Never run
`startup/scripts/sync_chat_nextseek.sh`: the canonical clone differs in **213 files** and
is on a divergent line; syncing would revert a deliberate v3-merge anchoring decision.
Every prior fix on this branch edited the snapshot directly (`2ef28d8`, `f5a13c9`,
`d89a415`).

**A local stack is running.** You need it for the container lane. **Do not rebuild or
restart it.** The operator drives Docker on this box and other sessions share it.

### Test lanes, both verified

*Host*, for `chat_nextseek/`:
```bash
cd chat_nextseek && uv run --no-project --with pytest --with pydantic --with requests \
  python -m pytest tests/<sel> -q -p no:cacheprovider
```

*Container*, for `nextseek_api/`:
```bash
docker cp nextseek_api nextseek:/app/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/assistant/tests/<sel> --no-migrations -q -p no:cacheprovider'
```

Plain `uv run pytest` fails on the host (`mysqlclient` will not build). Always copy source
alongside tests, or you test the deployed image's older code — that produced a phantom
failure during planning.

---

## The hard rule for this plan

**No live mutation. Ever.** Every test is unit-level against a stubbed transport. Nothing
you write may issue a real `POST`, `PATCH` or `DELETE` at any stack, local or remote. The
local stack holds a copy of production data: 50,887 samples, real scientist names, real lab
attributions.

A related hazard worth knowing: the harness's `route_gate` cases *look* free but actually
run a full server-side turn, because `http_driver` stops the **client** poll loop while the
server completes the turn on a daemon thread with no cancel path. So "I only asked for the
route" is not a safety argument anywhere in this system. Assume any request you send is
fully executed.

---

## What is already established

You do not need to re-derive the E1 chain, but the plan asks you to **verify** it, and you
should. Trust-but-check is the norm here: two of the four documents behind this work
contained claims that were false at HEAD.

**The five links:**

| step | location | what it does |
|---|---|---|
| 1 | `prompts/parser_core_routing.txt:77` | permits *"Updating or patching a specific sample record by UID"* |
| 2 | `context/min_api_endpoints_enriched.json` | advertises `DELETE /nextseek_api/samples/{uid}/`, *"Permanently delete a sample by UID"*, `intent_patterns: ['delete','remove']` |
| 3 | `agents/api.py:217-222` | resets the method **only when not in the endpoint's allowed list** — DELETE *is* allowed there, so the guard sanctions it |
| 4 | `helpers/tools/nextseek_api.py:54` | `requests.request(method=method, …)`, no filter |
| 5 | `nextseek_api/assistant/write_gate.py` | exists, but imported **only** by `assistant/granular.py:24`, a different ViewSet |

**The catalog, enumerated 2026-08-03.** 13 endpoints: 7 GET, 4 POST, 1 PATCH, 1 DELETE.
The critical subtlety is that **POST is used for both search and create**:

- read POSTs: `/admin/samples/retrieve/`, `/sample_types/get_parents/parents_by_child_types/`, `/samples/advanced_search/`
- write POST: `/samples/` (creates a sample)

So a method-only allowlist is wrong in both directions. The rule has to be
(method, path) pairs. Exactly three pairs mutate.

**The asymmetry that justifies the fix.** Neo4j is hard-blocked from writing by a regex
before the driver opens (`helpers/tools/neo4j.py:72-78`). REST has no equivalent. The
corpus already encodes the intended policy — writes route to `container_cc`, where
`nextseek-api-write` exits `WRITE_BLOCKED` without `--confirmed-write` and the skill
demands plain-text confirmation first. NS being read-only is not a new policy; it is the
policy that was already written down everywhere except in the code.

---

## Traps

**1. Do not wire the existing `write_gate.py` into `run_query`.** It is a confirmation
gate, and the NS path has no confirmation UI to gate against. A read-only chokepoint is
simpler and stronger. Writes belong to CC, which already has the gate and the UI.

**2. Do not change `is_staff` at sync time.** `dmac/views.py:80,97` set it for every synced
user. It is likely load-bearing for Django admin access, and its blast radius extends well
past the assistant. The narrow fix is at the data-scope decision (`nextseek_api/views.py:603`),
not at the identity.

**3. Task 5 may be untestable on this dataset.** Ground truth verified 2026-08-03: there is
exactly **one** project (`Published Data`) and six investigations. A project-scoping test
may be structurally unable to distinguish scoped from unscoped here. If so, say that
plainly rather than writing a test that passes vacuously. A vacuous security test is worse
than none, because it reads as coverage.

**4. Leave the WebSocket Origin check alone.** `consumers.py:39-52` returns `True` when
`Origin` is absent. That is defensible for non-browser clients, and it is not the hole once
authentication is enforced. Tightening it would also break
`chat_nextseek/e2e/playwright/runner.py:54`, which browses `http://nextseek_nginx` from an
origin that is not allowlisted.

**5. Prompt changes are invisible locally.** Prompts load once into `ChatConfig` at process
start (`config.py`), so `parser_core_routing.txt` edits are inert until a rebuild. Do not
claim the routing changed; say it is unverified until the post-merge run.

**6. Check the harness before changing the credential fallback default.** `nessie_tests`
authenticates as `demo:demopassword` over HTTP Basic, which supplies credentials explicitly
and should therefore be unaffected. Verify that before flipping a default, and if the
harness does depend on the silent fallback, default the setting on and file it as a
follow-up rather than breaking the only test lane the project has.

---

## Cross-plan constraints

Two other agents work in parallel worktrees off the same branch, split by file ownership.

**Plan 2 also edits `chat_nextseek/`, in different files.** They own
`schemas/schema_helper.py`. You own `helpers/tools/nextseek_api.py`, `context/`,
`prompts/parser_core_routing.txt` and `orchestrator.py`. No overlap.

**Plan 1 and you are a deliberate TDD pair.** Plan 1's task 8 adds a delete-refusal corpus
case; your task 2 closes the delete path. The case is meant to be observable as RED before
your fix and GREEN after. It only exercises against a live stack, so the transition is
observed post-merge, post-rebuild — you do not need to coordinate, and you must not add the
corpus case yourself.

---

## Definition of done

1. Both lanes green over the files you own.
2. `DELETE`, `PATCH` and `POST /nextseek_api/samples/` are refused by
   `tool_nextseek_api_request` with **no network call made** — assert on the absence of the
   call, not just on the exception.
3. All three search POSTs and the GETs still work.
4. An unknown path with a mutating method is denied. Default-deny is the property that
   survives the catalog changing.
5. An unauthenticated WebSocket connection with a valid `task_id` is rejected; the owner
   still receives the stream.
6. No live mutation occurred at any point.
7. One commit per item; scopes `security`, `api`, `assistant`.
8. **Do not merge. Do not push.**

---

## If you get blocked

- **Task 5's blast radius looks large.** Stop and report. Shipping four of five items with
  a clear note beats shipping five with one of them half-understood. This is explicitly
  sanctioned.
- **You cannot write a non-vacuous test for something.** Say so. Do not write the vacuous
  one.
- **A change wants a file you do not own.** Stop, note it, leave it.
- **The local stack is down.** Do not start or rebuild it. Report it.
- **You think a finding is wrong.** Check it and say so with evidence. The endpoint table in
  your plan is the one thing most likely to have drifted, and the plan already tells you
  your enumeration wins over the table.

---

## Background reading, in order

1. `docs/archive/2026-08/2026-08-03-nessie-hardening-design.md` §8 — the full argument, with every citation
2. `chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py:72-78` — the read-only guard you
   are mirroring, and the best model for what yours should look like
3. `chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py` — the whole file; it is
   short and it is where the boundary goes
4. `nextseek_api/assistant/consumers.py:36-100` — the Origin check and the ownership check
5. `docs/archive/nessie-corpus-review-findings-2026-07-30.md` — standing context on how little the
   corpus can currently detect, which is why item 1 went unnoticed
