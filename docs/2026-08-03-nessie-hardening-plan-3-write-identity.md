# Plan 3 — The write and identity boundary

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`. Steps use `- [ ]` checkboxes. Read
> `docs/2026-08-03-nessie-hardening-handoff-3-write-identity.md` **first**.

**Design:** `docs/2026-08-03-nessie-hardening-design.md` §8 (E1, E2)
**Branch:** cut a worktree from `dev-v3-merge`
**Owns, and nothing else:**

```
chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py         E1 chokepoint
chat_nextseek/src/chat_nextseek/context/min_api_endpoints_enriched.json  E1 catalog
chat_nextseek/src/chat_nextseek/prompts/parser_core_routing.txt       E1 prompt
chat_nextseek/src/chat_nextseek/orchestrator.py                       E2.2 credentials
nextseek_api/assistant/consumers.py                                   E2.3 websocket
nextseek_api/views.py                                                 E2.1 data scope
chat_nextseek/tests/, nextseek_api/assistant/tests/                   tests
```

**Goal:** close a real mutation path and three identity gaps. This is the only track in
the hardening effort that changes a security boundary, and it is sequenced last for that
reason. **Ship each item as its own commit.** Do not batch them.

---

## Global constraints

- **`chat_nextseek/` is a vendored snapshot. Edit it IN PLACE.** Do **not** run
  `startup/scripts/sync_chat_nextseek.sh`. The canonical clone at
  `/home/cdemu/code/chat_nextseek` is on branch `cd-dev` with **213 file differences**;
  syncing reverts the v3-merge anchoring. In-place editing is the established practice
  here (`2ef28d8`, `f5a13c9`, `d89a415`).
- **Do not touch `nessie_tests/`** (plan 1) or `schemas/schema_helper.py`,
  `services/cc_assistant.py`, `route_capabilities.json` (plan 2).
- **Test lanes**, both verified 2026-08-03:

  *Host* (for `chat_nextseek/`):
  ```bash
  cd chat_nextseek && uv run --no-project --with pytest --with pydantic --with requests \
    python -m pytest tests/<sel> -q -p no:cacheprovider
  ```
  *Container* (for `nextseek_api/`, local stack must be up — do not start or rebuild it):
  ```bash
  docker cp nextseek_api nextseek:/app/
  docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
    sh -c 'cd /app && uv run pytest nextseek_api/assistant/tests/<sel> --no-migrations -q -p no:cacheprovider'
  ```
  Always copy source alongside tests, or you test the deployed image's older code.
- **No live mutation, ever.** Every test here is unit-level with a stubbed transport.
  Nothing in this plan may issue a real `POST`, `PATCH` or `DELETE` against any stack.
- **Commit per item.** Scopes: `security`, `api`, `assistant`.

---

## Task 1 — Baseline

- [ ] Run both lanes over the directories you own. Record counts.
- [ ] Read `docs/2026-08-03-nessie-hardening-design.md` §8 in full.
- [ ] Confirm the claim before you fix it. All five links in the chain are verifiable
      by reading; do not take them on trust:
      - `prompts/parser_core_routing.txt:77` permits *"Updating or patching a specific
        sample record by UID"*
      - `context/min_api_endpoints_enriched.json` advertises
        `DELETE /nextseek_api/samples/{uid}/`, *"Permanently delete a sample by UID"*,
        `intent_patterns: ['delete', 'remove']`
      - `agents/api.py:217-222` resets the method to the endpoint default **only when it is
        not in that endpoint's allowed list** — and for this endpoint DELETE *is* the
        allowed method, so the guard sanctions it
      - `helpers/tools/nextseek_api.py:54` issues `requests.request(method=method, …)`
        with no filter
      - `nextseek_api/assistant/write_gate.py` exists but is imported **only** by
        `nextseek_api/assistant/granular.py:24`, which serves a different ViewSet

---

## Task 2 — E1: make the NS REST tool read-only

**Design principle.** The corpus already encodes the intended policy: writes route to
`container_cc`, where `nextseek-api-write` exits `WRITE_BLOCKED` without an explicit
`--confirmed-write` and the skill requires plain-text confirmation first. So the NS REST
tool should be **read-only, full stop** — mirroring Neo4j, which is hard-blocked by a regex
before the driver even opens (`helpers/tools/neo4j.py:72-78`). The asymmetry between the
two backends is the bug.

**The rule must be (method, path) pairs, not method alone.** The catalog holds 13
endpoints and POST is used for *both* search and create:

| verdict | method | path |
|---|---|---|
| allow | GET | all 7 GET endpoints |
| allow | POST | `/nextseek_api/admin/samples/retrieve/` |
| allow | POST | `/nextseek_api/sample_types/get_parents/parents_by_child_types/` |
| allow | POST | `/nextseek_api/samples/advanced_search/` |
| **deny** | POST | `/nextseek_api/samples/` — creates a sample |
| **deny** | PATCH | `/nextseek_api/samples/{uid}/` |
| **deny** | DELETE | `/nextseek_api/samples/{uid}/` |

A method-only allowlist would either block the three search POSTs or permit sample
creation. Exactly three endpoint-method pairs mutate; that is the denial set.

- [ ] **Verify the table above yourself** by enumerating the catalog. If it has drifted,
      the table is wrong and your enumeration wins. Record what you found.
- [ ] Write the failing tests in a new `chat_nextseek/tests/test_api_tool_read_only.py`:
      - `DELETE /nextseek_api/samples/{uid}/` raises and issues **no** network call
      - `PATCH /nextseek_api/samples/{uid}/` likewise
      - `POST /nextseek_api/samples/` likewise
      - each of the three search POSTs still succeeds
      - a GET still succeeds
      - an unknown path with a mutating method is **denied** (default-deny, not
        default-allow — this is the property that matters when the catalog changes)
      Stub the transport; assert on "no call was made", not just on the exception.
- [ ] Implement the guard in `helpers/tools/nextseek_api.py`, **before** the
      `requests.request(...)` at `:54`. Raise in the same shape the Neo4j guard raises so
      callers and the orchestrator's error handling behave consistently. Make the error
      message name the method and path, so a blocked turn is diagnosable from
      `console.txt`.
- [ ] Commit: `fix(security): make the NExtSEEK REST tool read-only on the NS path`.

Then defence in depth, as two further commits. The chokepoint above is the boundary; these
two reduce how many turns reach it and fail.

- [ ] Remove the `DELETE /nextseek_api/samples/{uid}/` entry from
      `context/min_api_endpoints_enriched.json`, and the `PATCH` entry with it. The agent
      cannot select what it cannot see. Check whether `agents/api.py` or any test asserts
      the endpoint count; update if so.
      Commit: `fix(api): stop advertising sample mutation endpoints to the API agent`.
- [ ] Narrow `prompts/parser_core_routing.txt:77` so update intent no longer routes into
      the REST corridor. The line currently reads *"Updating or patching a specific sample
      record by UID."* Removing it is not sufficient on its own — check the `unsupported`
      rules in the same file actually claim write intent, and add it there if not.
      **Note:** prompts load once into `ChatConfig` at process start, so this is only live
      after a rebuild. It is unverifiable locally; say so.
      Commit: `fix(prompts): route sample mutation away from the REST corridor`.

---

## Task 3 — E2.3: require authentication on the progress WebSocket

Cheapest of the three identity fixes and the most clearly wrong today. Do this one first.

**Problem.** `nextseek_api/assistant/consumers.py:97` reads:

```python
if user and user.is_authenticated and task.user_id != user.pk:
    return None
```

Ownership is checked **only if** a user is authenticated. With no authenticated user the
test is skipped and the connection is accepted, delivering the full progress stream and the
final answer payload to anyone holding the task UUID. The HTTP fallback for the same data
(`nextseek_api/services/assistant.py:924-938`) enforces both auth and ownership, so the two
paths disagree.

- [ ] Write failing tests in `nextseek_api/assistant/tests/`:
      - unauthenticated connection + valid `task_id` → rejected
      - authenticated non-owner + valid `task_id` → rejected
      - authenticated owner → accepted, still receives the stream
- [ ] Change the condition to require an authenticated user **and** ownership.
- [ ] **Leave `_is_allowed_origin` alone.** It returns `True` when `Origin` is absent
      (`consumers.py:39-52`), which is defensible for non-browser clients and is not the
      hole once auth is enforced. Changing it would also break
      `chat_nextseek/e2e/playwright/runner.py:54`, which browses `http://nextseek_nginx`.
- [ ] Confirm the nessie harness is unaffected: `nessie_tests/http_driver.py` polls over
      HTTP and never opens a WebSocket. Verify by reading, and state it in the commit body.
- [ ] Container lane green. Commit: `fix(security): require auth and ownership on the progress websocket`.

---

## Task 4 — E2.2: make the service-account fallback loud

**Problem.** `orchestrator.py:427-432` overrides `config.API_USER` / `API_PASS` only
`if credentials:` and only for truthy keys. With absent session credentials the turn
proceeds as whatever `ChatConfig` was built with — in the shipped template, the
`demo`/`demopassword` service account. The turn does not fail and does not warn, so a
query silently runs as a different identity than the asking user.

- [ ] Write a failing test: `run_query` with no credentials emits a warning naming the
      account in use. Use the existing `chat_nextseek/tests/` style; stub as needed so no
      LLM or network call happens.
- [ ] Log a warning at that seam identifying the fallback account. Do not log the password.
- [ ] Gate the silent substitution behind a setting that defaults to **off** outside a demo
      deployment, so a production install fails the turn rather than impersonating.
      Name it explicitly (for example `NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK`) and thread
      it from settings rather than reading the environment inside the orchestrator.
- [ ] Check whether the local stack and `nessie_tests` rely on the fallback: the harness
      authenticates as `demo:demopassword` over HTTP Basic, which supplies credentials
      explicitly and is therefore unaffected. **Verify this** before changing the default,
      and if the harness does depend on it, default the setting on and note it as a
      follow-up rather than breaking the test lane.
- [ ] Host lane green. Commit: `fix(security): warn and gate the service-account credential fallback`.

---

## Task 5 — E2.1: stop treating staff as superuser for data scope

**This is the highest-risk change in the entire hardening effort. Its own commit, its own
careful check, and it is fine to stop and report instead of shipping it.**

**Problem.** Every SEEK user synced into NExtSEEK is marked staff (`dmac/views.py:80,97`
set `user.is_staff = 1`). Then `nextseek_api/views.py:603` does:

```python
# Treat Django staff as admin for data scope, matching IsAdminUser
is_superuser = bool(getattr(request.user, 'is_superuser', False)
                    or getattr(request.user, 'is_staff', False))
```

and takes the unfiltered branch of `getChildrenUIDs` (`seek/dbtable_sample.py:900-905`).
So **project membership does not restrict what the assistant returns for anyone.**

- [ ] **Establish the blast radius first.** Find every caller that depends on the
      staff-as-admin behaviour, in `nextseek_api/` and in `seek/`. Write down what you
      found. If the answer is "more than the retrieve path", stop and report rather than
      shipping a partial change.
- [ ] **Do not change `is_staff` at sync time** (`dmac/views.py:80,97`). It is likely
      load-bearing for Django admin access and its blast radius extends well outside the
      assistant. The narrow fix is at the data-scope decision, not at the identity.
- [ ] Write failing tests: a non-superuser staff user's retrieve is scoped to their
      projects; a superuser's is not.
- [ ] Change `views.py:603` to use `is_superuser` alone for data scope, and update the
      comment to say why staff is deliberately no longer sufficient.
- [ ] Check the seeded local data can actually express the difference. The ground truth
      recorded on 2026-08-03 is that there is exactly **one** project (`Published Data`)
      and six investigations, so a project-scoping test may be structurally unable to
      distinguish scoped from unscoped on this dataset. If so, say that plainly rather than
      writing a test that passes vacuously.
- [ ] Container lane green. Commit: `fix(security): scope assistant retrieval by superuser, not staff`.

---

## Task 6 — Final verification

- [ ] Both lanes green over the files you own, counts recorded.
- [ ] Confirm no live mutation occurred anywhere during this work.
- [ ] `git diff --name-only dev-v3-merge...HEAD` shows nothing under `nessie_tests/` and
      none of plan 2's files (`schemas/schema_helper.py`, `services/cc_assistant.py`,
      `dmac_assistant/build_context/route_capabilities.json`).
- [ ] One commit per item, five or six total.
- [ ] Completion note covering:
      - the endpoint table as you actually enumerated it
      - what depends on staff-as-admin, and whether you shipped task 5 or stopped
      - which changes are unverifiable until a rebuild (the prompt change, certainly)
- [ ] **Do not merge. Do not push.**

---

## What this plan deliberately does not do

- Does not wire `nextseek_api/assistant/write_gate.py` into `run_query`. The read-only
  chokepoint is a simpler and stronger boundary than a confirmation gate, because the NS
  path has no confirmation UI to gate against. Writes belong to CC, which already has one.
- Does not change `is_staff` at user sync.
- Does not add a delete-refusal corpus case. That is plan 1's task 8, deliberately — the
  case is meant to be observable as RED before this plan lands and GREEN after.
- Does not touch SEEK's own `SampleProxyViewSet.destroy` (`nextseek_api/services/samples.py:342`).
  It is reachable by an authenticated API client independently of the assistant, and
  narrowing it is a separate decision with its own consumers.
