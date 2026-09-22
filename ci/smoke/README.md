# Post-deploy smoke suite

Runs **outside** the container, against a deployed stack, over HTTP, the way a
user does. Same command locally, on fairdata-dev, and in CI.

## Run it

```bash
# ./startup.sh ci runs this itself before every run (a no-op once the browser is there).
# By hand, per account that runs the suite (browsers live in ~/.cache/ms-playwright):
uv run --no-project --with playwright==1.60.0 playwright install chromium

# everything except the write lane. That includes the Nessie lane, which sends
# four real chat turns (about $0.30); add --no-nessie to skip it.
CI_BOX_PROFILE=local \
uv run --no-project --with pytest --with requests --with playwright==1.60.0 \
  pytest ci/smoke/ --base-url http://127.0.0.1:8000
```

`CI_BOX_PROFILE` is not decoration: an absent value means `prod`, and `--profile`
can only narrow, so pasting this line without it runs a laptop as the most
restrictive profile with no way to widen. `./startup.sh ci` sets it for you.

Useful flags:

| flag | what it does |
|---|---|
| `--wait-ready` | Run the readiness gate first. Use after a rebuild; skip it for local iteration or you wait out the 300s floor every time. Before the floor it connects to `--base-url` once, and a refused connection exits at once naming `nextseek_nginx`: the floor waits for an app that is still starting behind a live nginx, and no amount of waiting fixes a front door that is not there. The floor is a visible countdown (a `[readiness]` line every 30 s), then one line per probe, then `ready after N s`; those lines are written with pytest's capture suspended, because from a fixture a plain reporter write is swallowed on a real terminal. The gate probes an authenticated endpoint, so with no `CI_SMOKE_USER`/`CI_SMOKE_PASS` it exits 2 rather than skipping: a caller that asked for a gate must not read "0 tests, exit 0" as a pass. Without the flag, missing credentials still degrade to per-test skips. |
| `--ready-floor N` | Seconds before the first probe (default 300). |
| `--strict-console` | Fail a flow on any console error not in `CONSOLE_ALLOWLIST`. Off by default so early runs report what is actually there. |
| `--headed` | Watch the browser. |
| `-m write` | Run only the write lane. It is deselected otherwise. |
| `--no-nessie` | Skip the Nessie lane (`test_nessie.py`). The only supported opt-out: never skip it with `-m "not nessie"`, because any `-m` expression switches the write lane back on. |
| `--nessie-no-turns` | Skip the Nessie lane's chat turns, so only its stage 1 runs (no model spend). The rest of the suite is unaffected; to iterate on the lane, name `test_nessie.py` as well. |

`./startup.sh ci` is the operator entry point. It runs exactly this command
against the instance's own port, and derives the profile from `ci_profile` in
`startup/.instance.json`, so nobody has to remember which box they are on.

Nine files need no stack, no credentials and no browser, because they test the
registry, the guard, the fixtures' own logic and the Nessie and write lanes' pure
helpers rather than a deployment: `test_registry_unit.py`, `test_registry_contents.py`,
`test_guard_unit.py`, `test_profile_unit.py`, `test_assertions_unit.py`,
`test_readiness_unit.py`, `test_terminal_unit.py`, `test_nessie_unit.py`,
`test_attribute_jobs_unit.py`.

```bash
CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests \
  pytest ci/smoke/test_registry_unit.py ci/smoke/test_registry_contents.py \
         ci/smoke/test_guard_unit.py ci/smoke/test_profile_unit.py \
         ci/smoke/test_assertions_unit.py ci/smoke/test_readiness_unit.py \
         ci/smoke/test_terminal_unit.py ci/smoke/test_nessie_unit.py \
         ci/smoke/test_attribute_jobs_unit.py -q
```

## Tiers

`test_reachability.py` is T0: one test per registry route, parametrised at
collection from `ci/routes.py`. It asserts a status, a live gateway and no
silent bounce to the login page, and it grows by itself as the registry does.

Everything else is hand-written because it is what a table row cannot express --
the API root's exact viewset list, the OpenAPI document generating at all, an
enrichment step that fails silently behind a 200, the five `/seek/` pages that
must bounce a visitor with no credentials, the seven browser flows, a
`samples/graph_search/` POST with its envelope checked (`test_graph_search.py`), and the
state of the graph sync itself (`test_graph_sync_status.py`, below). Per-route
body assertions are T1's job and are not in this increment.

## Nessie lane

`test_nessie.py` proves one thing: **this build did not break Nessie.** Every
Nessie route and chat-page control exists and answers, three NS questions and one
CC question complete through the real chat page, and the sessions endpoints
report what the page showed. It does not judge whether an answer is right. Answer
accuracy belongs to `NessieAI/tests/nessie_tests/`, which is run by hand.

It works in three stages, all in that one file:

1. **Everything exists, with no model call.** The write account is a superuser
   in a participating project. `/seek/assistant/` renders the chat input, the
   send button, New chat, the saved-chats sidebar, the upload control, the Debug
   panel, the admin route override, and the JSON and Metadata buttons (present
   and disabled). Every GET route in `ci/routes.py` under `assistant/`,
   `cc-assistant/`, `nessie/`, `evaluator/` and `schema_rag/` answers its
   declared status. The session, test-case and upload lists answer,
   `schema_rag/retrieve/` returns endpoints, and a scratch chat is created,
   renamed and deleted.
2. **Four questions, typed into the page, in one chat, in this order:** "What
   can you do?", "What mice are treated with NDMA?", "What studies are in
   IMPACT?" (NS), then "Make me a histogram image of NHP species" (CC). None is forced, so
   the real router picks each engine. NS goes first and CC last, because a
   completed CC turn makes the chat sticky and CC would then answer every later
   question.
3. **The API agrees with the page.** Each turn ran on the engine it should.
   Question 2 took the API path and question 3 the graph path. The JSON,
   Metadata and CC artifact downloads work. The session detail and
   `nessie/sessions/<id>/debug/` count four turns, with the replies and the route
   ledger the page showed. After a reload the chat reopens with every turn. The
   reported spend stayed under the ceiling.

The chat is kept whether the lane passed or failed, and the CI record names it as
the kept session. On a failure it is kept as it is, so that it can be read: any
failed test in the module counts, stage 1 included, because the failure count is
taken before the lane's first test. On a pass it is kept so that a later
intermittent failure has a passing chat to be diffed against: the lane titles it
`CI Nessie lane passed <UTC time>` (`PASSING_CHAT_TITLE`), then deletes the write
account's older chats with that title beyond `KEEP_PASSING_CHATS` (3), the one it
just kept included. So the write account's saved-chats sidebar holds the three
newest passing chats, plus every failing chat until someone deletes it by hand.
The lane finds the old ones in `assistant/sessions/`, which lists an account's
newest 50 chats. A passing chat the lane cannot title is deleted instead, because
no later run could find it to prune it. Anything that did not work (the title, a
DELETE, the list) is named as "Cleanup failed" in the CI record's Nessie section,
and in pytest's warnings summary on a direct run.

### When it runs

- After `./startup.sh rebuild` of the app (a bare rebuild, `--component app` or
  `--component nextseek`) and on every `./startup.sh ci`, on a box declaring
  `local` or `dev`.
- Never on `prod`. Startup passes `--no-nessie` there, and the module's
  `profiles("local", "dev")` marker skips it anyway.
- A component rebuild (`cc-agent`, `bedrock-proxy`, `nextseek-sidecar`,
  `custom-stack`) skips it, so a run of component rebuilds does not pay for the
  same four questions each time. Run `./startup.sh ci` after the last one.
- `--no-nessie` skips it. `./startup.sh rebuild` and `./startup.sh ci` take it
  as their own flag and pass it through; a direct `pytest ci/smoke/` run takes it
  as a suite option; the GitHub dispatch workflow has a `nessie` input, default
  on.
- It costs about $0.30 a run. The CC turn's observed mean is $0.24; it may
  report up to $0.50 for each minute it ran (never less than $0.50), and the
  three NS turns cost a few cents that
  nothing measures yet. At most four chat POSTs leave the page; the browser
  aborts a fifth and the lane fails. Reported spend above $1.00 fails the run.
- It adds about 4 to 5 minutes to a rebuild's CI. Each NS turn may take 300 s,
  the CC turn 240 s, and the lane stops asking questions at 720 s.

### Prerequisites

- **The write account.** `CI_WRITE_USER` and `CI_WRITE_PASS` in
  `~/.config/nextseek/ci.env`. The lane drives the page and the admin checks as
  this account, so it must be a superuser and a member of a SEEK project listed
  in `ASSISTANT_PARTICIPATING_PROJECTS` (`dmac/local_settings.py`). Without that
  membership the assistant answers 403 and no turn can be sent. Like every CI
  account, it must have logged in through `/login/` once, by hand.
- **The smoke account.** `CI_SMOKE_USER` and `CI_SMOKE_PASS`, the existing
  non-superuser. The lane's smoke-auth and web-auth checks run as this account,
  and it proves a non-superuser gets `is_admin: false`. If it is missing, those
  checks fail rather than skip.
- **The Bedrock proxy token.** A non-empty `AWS_BEARER_TOKEN_BEDROCK` in
  `NessieAI/docker/bedrock-proxy/proxy-secret.env`. The CC turn reaches the model only
  through the proxy.
- **The CC runtime.** The `cc-agent` image (`dmac-assistant:poc`) is present,
  `bedrock-proxy` and `nextseek-sidecar` are running, and the app container can
  spawn a CC turn (`cc_engine.cc_runner_available()`).

With the lane on, startup checks the proxy token, every first-party image, the
two CC services and the CC runner right after stack health. It prints one line
per check and stops before the suite starts, naming the one that is missing. It
checks only that the token is non-empty, and never prints it. The accounts are
checked by the lane itself: its first stage 1 tests fail, naming the fix, when
the credentials are missing, the account is not a superuser, or it is outside
every participating project. They fail rather than skip, because a skipped lane
would read green on a misconfigured box.

### Extending it

When Nessie changes, `test_nessie.py` changes, and nothing else should need to.

- **A new question** is a row in `QUESTIONS`: its text, the engine the router
  must pick, the path it must take (`system`, `api`, `graph` or `cc`), and
  whether it leaves a bundle or a CC artifact. Keep the NS questions before the
  CC one. The chat POST limit follows the table's length; the $1.00 ceiling does
  not.
- **A new endpoint** is an entry in `ci/routes.py` plus a check in
  `test_nessie.py`. A GET route with a literal path under one of the five
  prefixes above reaches stage 1's reachability check with no code at all.
- **`--nessie-no-turns`** skips only the lane's chat turns: stage 1 runs, nothing is
  spent, and the rest of the suite is unaffected. Iterate with the command below.

```bash
CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests --with playwright==1.60.0 \
  pytest ci/smoke/test_nessie.py --base-url http://127.0.0.1:8000 --nessie-no-turns -q
```

### Reading a failure

- **The CI record.** `./startup.sh rebuild` and `./startup.sh ci` write a record
  under `startup/ci-reports/`. Its Nessie section has one row per question
  (route, source, path, seconds, cost, status, task id) and the reported spend,
  and on a failure the kept chat's session id, its `/debug/` URL and the evidence
  folder. A section reading "No summary" means the lane stopped before its first
  question or in its own cleanup; the record's Failures and Errors sections name
  the fixture that did it.
- **The kept chat.** Open its `/debug/` URL
  (`/nextseek_api/nessie/sessions/<id>/debug/`) as a superuser: it lists every
  turn, the route ledger, the CC transcript, the files and any warnings. The chat
  also stays in the write account's saved-chats sidebar. To tell an intermittent
  failure from a steady one, open the newest `CI Nessie lane passed` chat beside
  it and compare the same turn in both. Delete a failing chat when you are done;
  the passing ones are pruned by the lane.
- **The evidence folder**, `startup/ci-reports/<label>-nessie/`, written only on
  a failure: `trace.zip` (a Playwright trace of the whole browser session),
  `page.png` (the page as the lane left it) and `debug.json` (the `/debug/`
  answer with `?include=all`). Open the trace with
  `npx playwright show-trace <trace.zip>`, or, with no Node on the host,
  `uv run --no-project --with playwright==1.60.0 playwright show-trace <trace.zip>`.

A direct `pytest` run, the GitHub workflow included, writes no record. Its
evidence goes to a `nessie-evidence` folder under pytest's base temporary
directory, or wherever `CI_NESSIE_EVIDENCE_DIR` points, and the kept chat's id is
inside that folder's `debug.json`.

## Graph sync status

`test_graph_sync_status.py` reads `GET /nextseek_api/admin/graph-sync/status/` and asserts
two things. The endpoint reports the latest run of each kind, freshness per job, the outbox
and the last drift result; and **none of those jobs is stale**. A box whose sync loop has
stopped, or whose drain has left an outbox row waiting for more than an hour, must not
report a green smoke run. A box that has never run a sync answers `never`, which stays
green, so the tests assert the vocabulary rather than a particular value.

It also carries the parity-lite check: when the status reports a successful full sync at the
writer's schema version, the same small body sent to `samples/advanced_search/` and to
`samples/graph_search/` must report the same `total`. Before the first full sync the graph
is at an earlier schema version and the two are expected to disagree, so that check skips
rather than failing a box that is simply not synced yet.

**It needs the superuser account, and it runs in the default lane.** The endpoint is
superuser-only, so this module authenticates with `CI_WRITE_USER` and `CI_WRITE_PASS` and
**fails rather than skips** when they are missing: it is the first test outside the opt-in
write lane to need them, and a skip would let a box with no superuser credentials report
green having proved nothing about the one endpoint no other account can reach. It sends a
GET and two searches, writes nothing, and carries no `write` marker.

`local` and `dev` only, like the route: production runs a v1.0 graph without migration
0021, so the two tables the endpoint reads are not there at all.

## The behavioural lane

`test_graph_behaviour.py` is the only module that asserts **the graph changed** after a write.
Everything else about graph sync proves wiring: `ci/writers.py` declares all 30 writer sites and
`ci/gate/test_writer_registry.py` fails when one appears without a hook, which proves a writer calls
something and nothing about Neo4j.

**Opt in twice**, like the write lane: `-m graphwrite`, plus `CI_WRITE_DESTRUCTIVE=1` for the cases
that mutate rows. From a worktree, add `--force-profile local CI_FORCE_PROFILE_CONFIRM=yes`, because
`startup/.instance.json` lives in the checkout the stack runs from and the guard fails closed to
`prod` without it.

```bash
CI_FORCE_PROFILE_CONFIRM=yes CI_WRITE_DESTRUCTIVE=1 uv run --no-project --with pytest \
  --with requests --with playwright pytest ci/smoke/test_graph_behaviour.py -m graphwrite \
  --base-url http://127.0.0.1:8000 --force-profile local
```

### Two gates, then the cases

The module refuses to mean anything until both gates pass: the graph is at the writer's schema
version (below it the writer refuses every write, so each case would fail for the wrong reason), and
the sync loop drains the outbox **without help** — no case here issues a sync command. The gates also
assert the outbox holds no dead rows, because `wait_for_drain` reports a drain with dead rows present.

| Case | Writer | What the graph must do |
|---|---|---|
| batch upload | WR-01, WR-02 | the job's `totals.graph` says `synced`, and the graph matches both rows: the one path that syncs inline |
| attribute create and delete | WR-05 | the graph's **catalog** declares the attribute, then stops declaring it |
| sample update | WR-07 PATCH | the node matches the new value and stops matching the old one |
| delete | WR-13 | the node comes down by the retire rule |
| delete, through the API | WR-07 destroy | the node comes down even when SEEK outruns the proxy: see below |
| sample joins a project | WR-01, WR-02 | a scoped account that could not see the sample now can |
| person change | WR-10 | the `membership` row drains rather than dead-lettering |

### Three things that will mislead you

Each one reads as a product defect until you know about it.

- **`total` and `rows` can disagree, and only `total` is the graph's answer.** `total` is counted in
  Cypher; `rows` are that page hydrated from MySQL. A node the graph still holds whose MySQL row is
  gone answers `total: 1, rows: []`, and the response's `rows_missing` counts such matches on the
  page. So `graph_holds` is for **presence** only, and every absence assertion reads
  `graph_total`/`wait_for_total`. An absence assertion built on the rows passes on exactly the
  failure it exists to catch.
- **`graph_meta` is as fresh as the last drift run, and no fresher.** The status endpoint does not
  query Neo4j. Asserting that `catalog_hash` moved after a write compares a cached value with itself.
- **`graph_search` caches the catalog** for `RECHECK_SECONDS` (60) and re-reads it only when
  `GraphMeta.catalog_hash` moves, so a change that has genuinely landed can take a minute to show.
  The attribute case polls past that window.

### What it cannot assert, and where that is covered

`graph_search` answers about samples, so **`MEMBER_OF` is invisible to this lane**. The person case
proves the hook fires and the loop drains the kind; whether the graph's memberships match SEEK is
gate G's `people.*` check (`graph_sync/verify.py::_check_people`), which runs inside every drift run.
Asserting it here would need a Neo4j connection this lane may not open.

### Identity, and why not `people/current/`

The cases resolve accounts through `/nextseek_api/users/` (the admin list, read from SEEK's tables
through the ORM) and memberships through `/nextseek_api/people/<id>/` (the full `projects` set).
**Not `/nextseek_api/people/current/`**: that path resolves the caller through the SEEK proxy's
shared session, and six calls alternating the two smoke accounts answered with one identity for
both. A lookup that names its subject in the path is unaffected.

### The API-proxy delete is slow on purpose

SEEK's own delete can outrun `SeekAPIClient.timeout_s`, and Rails then completes it after the proxy
has given up. The proxy used to answer 500 and enqueue nothing, leaving a node `graph_search` counts
and cannot show. When SEEK does not answer in time the proxy now answers 202 with
`status: unconfirmed` and enqueues the retire held back by `UNCONFIRMED_RETIRE_DELAY_S`
(`nextseek_api/services/samples.py`), so the retire reads MySQL after Rails has finished. The case
therefore waits out that delay in its drain. The product's own UI does not use this path; the Sample
Deletion tab posts `alluids` to `/seek/samples/delete/` (WR-13).

### `/seek/samples/delete/` is enabled for `local` only

That route was `EXCLUDE_UNSAFE_METHOD` with no profile. The lane needs it, so it is now declared
`profiles="local", auth="write"`, and **never dev or prod**: the write it makes is irreversible data
loss rather than one of the safe previews. `auth="write"` keeps it out of the T0 sweep, which never
holds that account.

### What it leaves behind

Each case deletes its own samples through WR-13, in a `finally`. **The xfailed case leaves exactly
one orphan node per run**, unavoidably: the proxy removes the MySQL row after its timeout, so by the
time the cleanup runs there is no row for `getSampleID` to resolve the UID against, and the delete
that would enqueue the retire cannot find it. Measured 2026-09-17 over four runs: four orphans, one
each. Retire them with

```bash
manage.py graph_sync --samples <ids> --i-mean-the-live-graph
```

and find their ids by asking the graph rather than the endpoint, since `graph_search` counts them and
cannot show them. Nothing else accumulates: a case that borrows `a_throwaway_sample` and forgets its
`finally` leaves one row per run, which is how five of them appeared before the update case had one.

## Profiles

Every route in `ci/routes.py` names the profiles it may be called under, and the
client refuses anything else *before* the request leaves the process. **Adding a
route is one line in `ci/routes.py`**; T0 picks it up at the next collection and
nothing in this directory needs editing.

**The box declares its own profile** in `CI_BOX_PROFILE` (`local`, `dev` or
`prod`), and `./startup.sh ci` sets it for you. **An absent value means `prod`**:
a machine nobody has configured gets the most restrictive profile, never the
least.

| flag | what it does |
|---|---|
| `--profile NAME` | Narrow below what the box declares. Asking to widen exits 2 rather than running. |
| `--force-profile NAME` | Widen. Refused unless `CI_FORCE_PROFILE_CONFIRM=yes` is set too, and prints a banner when it runs. Never put this in a workflow file. |

Passing both exits 2 rather than deciding which one wins.

The profile gates whole tests as well as routes. A test marked
`@pytest.mark.profiles("local", "dev")` is **skipped** under any other profile.
Six places carry it today. Three are browser flows, each because the page issues a
POST. The first submits an upload for validation. Under `prod` the browser guard
aborts that POST at the network layer, correctly, and the page would then wait out
its own response timeout — five red minutes for a rule the suite had just enforced.
Skipping says the same thing in a line.

The other two flows drive the Sample Search page's Advanced and Simple boxes, which
search by POSTing to graph_search, an endpoint itself declared `local,dev`. The other
three places are whole modules: `test_nessie.py`, which
writes a chat and pays for model turns; `test_graph_search.py`; and
`test_graph_sync_status.py`, whose endpoint exists only where migration 0021 has been
applied.

## Credentials

Two accounts, and the split is a safety rule rather than hygiene. The sweep is,
by construction, a program that issues GETs at every URL it knows about, so it
never holds rights it does not need: the health sweep and the browser flows
authenticate as the non-superuser, and the sweep never requests any path under
`/seek/admin/`, at any privilege level. Which routes make that rule necessary,
and why, is recorded in the private findings note, which this public repository
does not carry. Two places are the exception, and both request nothing under
`/seek/admin/` either. The Nessie lane drives the chat page, the admin checks and the
superuser-only `/debug/` route as the write account, because those need a superuser. The
graph sync status check does the same for its one superuser-only endpoint, and unlike the
Nessie lane and the write lane it runs by default, so the superuser credentials are now a
prerequisite of an ordinary smoke run.

```
~/.config/nextseek/ci.env      mode 600, never committed, never in GitHub

CI_SMOKE_USER=...     NOT a superuser. Health sweep + the browser flows.
CI_SMOKE_PASS=...
CI_WRITE_USER=...     Superuser. The write lane, the Nessie lane, and the graph
CI_WRITE_PASS=...     sync status check, which runs in the default lane.
```

Environment variables override the file. `NEXTSEEK_CI_ENV` points at a different
file.

**Both accounts must log in through `/login/` once, by hand, on each box before
anything works.** `BasicAuthentication` validates against Django's `auth_user`
table, and only the login view creates that row. Until then every request is a
401 and the reason is not obvious.

## Two authentication modes, not interchangeable

| surface | how |
|---|---|
| `/nextseek_api/*` | HTTP Basic |
| `/seek/*` | a real session cookie from a POST to `/login/` |

`seek` views read `request.session['username']`, which Basic auth never
populates, so a Basic-authenticated request to a `/seek/` page returns a 302 to
`/login/`. A sweep that follows redirects reports that as 200 and calls the page
healthy. Hence `allow_redirects=False` everywhere, and two separate fixtures
(`api` and `web`) that must never share a session: DRF stops at the first
authenticator that succeeds, and a stray `sessionid` outranks the Basic header.

## Known conditions

Breakage that is real is pinned rather than hidden. Every pin reports `xfailed`
while the defect is there and **XPASS** the day it is fixed -- XPASS is the
signal to fix the declaration and delete the pin, and it is why none of these
can quietly turn into a pass.

Most of them now live in the registry and are reported one by one by T0: eleven
routes carry an `xfail` reason in `ci/routes.py` under `local` and `dev`, which a
`prod` box narrows to two (`/seek/searchAdvanced/` and
`/nextseek_api/entity_tree/nodes/`). A registry entry declares the
status a *working* route returns, never the status the broken one returns today,
which is what makes the flip work in both directions.

Two more are hand-written here, because what they assert is not a status code:

- `test_seek_identity_matches_the_authenticated_caller` — two different
  authenticated accounts are reported as the same SEEK person. Cause and fix
  are in the private findings note, not in this repo.
- `test_entity_tree_nodes` — sample types with no attribute definitions make the
  endpoint return an application-level 502.

## What a 200 does not prove

`check_gateway` distinguishes an nginx 502 (HTML, the stack is down, always a
failure) from an application 502 (a JSON envelope, a data condition). Beyond
that, a great many endpoints return 200 on failure: `schema_rag/retrieve/` always
does, roughly thirty `seek` paths return permission denials and wrong-method
errors as 200, `batch-upload/validate/` returns 200 for an invalid sheet, and a
SEEK outage becomes `total: 0` in several places. Every hand-written assertion
here checks a body, a header or a rendered element, not just a status; T0's
per-route checks are deliberately shallower, which is why T1 exists.

## Cost

The Nessie lane is the one part that spends money: about $0.30 a run (see
"Nessie lane" above), and `--no-nessie` skips it. Nothing else does. The chat-page
flow in `test_flows.py` proves the page is wired by asserting `?q=` hydration,
and where it exercises the send path it aborts the request in the browser before
it leaves. `batch-upload/validate/` involves no model call and
no INSERT.

One caveat on a shared box: validate always runs UID generation, which takes a
MySQL advisory lock, so it can contend briefly with somebody's live upload.
