# NExtSEEK CI/CD: full implementation spec

Date: 2026-09-01
Status: for review, not yet implemented
Supersedes: `2026-09-01-nextseek-ci-cd-design.md` (the approved design, whose five decisions
are carried forward unchanged)

This document is public. It names paths and assertions but deliberately carries no
reproduction detail for unresolved platform issues, per the tracking rule at `.gitignore:217`.
A separate, uncommitted findings note covers what is omitted here.

---

## 1. What changed since the design

The design was approved on reasoning. Everything below was then measured. Six results
change what gets built.

### 1.1 A bare `pytest` at the repo root runs zero tests

Collection produces 6 errors and pytest prints `Interrupted`, so the session ends before a
single test executes. Three of the six share one cause: `pytest_plugins` declared in a
non-top-level `conftest.py`, which current pytest rejects outright. The affected directories
are the main ones (`nextseek_api/tests`, `nextseek_api/attributes/tests`, `startup/tests`).

Consequences, both mandatory:

- CI must always pass **explicit test paths**.
- CI must pass **`--continue-on-collection-errors`**, and collection errors must be
  first-class entries in the baseline, not merely failures.

### 1.2 The GitHub-hosted job is viable, and was proven end to end

The concern was that the app cannot be stood up off-box. It does not need to be. Simulated
job 1 exactly as a runner would perform it: `git archive HEAD` into a clean directory (which,
being gitignored, has **no `dmac/local_settings.py`**), then `uv sync`, then `baml-cli
generate`, then the suite, on `ghcr.io/astral-sh/uv:debian` with no database and no network
access to anything.

| step | result |
|---|---|
| `uv sync`, cold cache | 44.7s |
| `baml-cli generate` | a few seconds |
| test run | 4m10s |
| outcome | 228 failed, 7663 passed, 52 skipped, 6 xfailed, 73 errors |

`dmac/test_settings.py` is what makes this work: it points both databases at in-memory SQLite
and stubs the settings that normally come from `local_settings.py`. No `libmysqlclient-dev`
is needed; the image installs nothing beyond `openssh-client` and the deps resolve as wheels.

Whole job, including checkout: roughly 6 minutes.

### 1.3 Counts lie, in a way that bit this measurement too

Two things to hard-code into the extractor.

**Log lines masquerade as results.** An anchored `grep '^FAILED\|^ERROR'` over the run output
also matches Django log records whose level is literally `ERROR`, for example
`ERROR    django.request:log.py:253 Internal Server Error:`. That inflated a count from 295
to 308. The extractor must be scoped to the `short test summary info` block only:

```bash
awk '/^=+ short test summary info =+$/{f=1;next} /^=+ .* =+$/{f=0} f' run.txt \
  | grep -E '^(FAILED|ERROR) ' \
  | sed -E 's/^(FAILED|ERROR) //; s/ - .*//; s/[[:space:]]*$//' \
  | grep -E '\.py(::|$)|/' \
  | sort -u
```

**Summary totals disagree with the summary block.** The run reported 73 errors while the
block listed 134 `ERROR` lines, because a single errored test contributes both a setup and a
teardown line. Compare deduplicated sorted IDs, never totals. This is ANN-2's method and it
is the only one that holds.

### 1.4 The baseline is bound to one exact command

Running the same lanes against the app image's virtualenv instead of a clean `uv sync`
produced a different result, and the difference was not noise. `chat_nextseek` and
`dmac_assistant` are **editable installs** pointing into the image at `/app/...src`, so
mounting a different tree silently tests the image's copy of that code.

Diffed by name: 10 `batch_upload` tests fail only under a clean sync (the image's venv was
masking them), 4 `schema_rag` integration tests fail only under the image's venv.

The committed baseline is therefore the clean-sync one, **301 unique IDs**, and its header
records the command verbatim. Regenerating it any other way invalidates it.

Four of the 301 are collection-level (a whole module or directory) and each hides an unknown
number of tests. One of them, `nextseek_api/attributes/tests`, is the lane that cannot run
off its author's machine.

### 1.5 Playwright has no browsers where the app runs

The Python package is present in the app image; the browser binaries are not. The repo
already documents the fix as a one-time host step (`chat_nextseek/README.md:176`,
`uv run playwright install chromium`).

That settles the architecture: **the smoke suite runs on the runner host, not inside the
container**, hitting the stack over HTTP the way a user does. It needs `pytest`, `playwright`
and `requests`, and none of the application's 67 dependencies. Run it in an isolated
environment, mirroring the pattern the startup CLI already uses for the same reason:

```bash
uv run --no-project --with pytest --with playwright --with requests \
  pytest ci/smoke/ --base-url https://<host>
```

One command, identical locally and on the box.

### 1.6 The reference defect is a one-line ordering bug

`themes/NextSeek/templates/base.html`:

```html
31  <script src="{% static 'jquery-easyui-1.5.2/jquery.min.js' %}"></script>
32  <script src="{% static 'js/easyui/datagrid-filter.js' %}"></script>        <- extension
33  <script src="{% static 'jquery-easyui-1.5.2/jquery.easyui.min.js' %}"></script>  <- plugin
```

The extension loads one line before the plugin it extends. Line 10 of `datagrid-filter.js` is
the file's first executable statement and reads `$.fn.datagrid.methods`, which is undefined at
that moment. All three files exist, so this is ordering and not a missing asset. Line 32 is
unconditional and outside any block, so **every page extending `base.html` throws exactly
once at head-parse time**.

Column filtering nevertheless works, by accident: ten templates re-load the same file in the
body, after the plugin, and that second execution installs the API. The app is one
"remove the duplicate script tag" commit away from a real ten-page breakage.

**Swap lines 32 and 33 before the console-error check goes in.** It is one line, it removes
the single error that would otherwise have to be allowlisted on every page, and it closes the
latent breakage. Doing it first means the console check starts from zero rather than from a
permanent exception.

---

## 2. Decisions

The five approved decisions stand. Three more were settled during this review.

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Runners | Hybrid: pytest on GitHub-hosted, health/smoke on self-hosted (fairdata-dev) | The stack only exists on the box; a red pytest job should not hide page health |
| 2 | Trigger | Operator rebuilds, then CI tests | CI never restarts a shared box under someone else's work |
| 3 | Write policy | Tiered: read-only by default, opt-in write lane | Safe to run constantly, still able to prove a write on demand |
| 4 | Test driver | Both: HTTP sweep for health, Playwright for flows | 200 does not mean working |
| 5 | pytest gate | Informational, never blocks | Operator's decision. Mitigated by baseline diffing, not overridden |
| 6 | **Accounts** | **Two: `ci_smoke` (non-superuser) and `ci_write` (superuser)** | See 2.1. Not merely hygiene here |
| 7 | **Credentials** | **`~/.config/nextseek/ci.env`, mode 600, on each box; env vars override** | Same file shape locally, on dev, on prod. Never stored in GitHub |
| 8 | **Smoke host** | **Runner host, not the app container** | Browsers are not in the image, and outside-in is the honest test |

### 2.1 Why two accounts is a safety rule, not hygiene

A health sweep is, by construction, a program that issues GETs at every URL it knows about,
so it must never hold rights it does not need. Which routes make that rule necessary, and
why, is recorded in the private findings note, which this public repository does not carry.

Two rules follow, and they are not negotiable:

- The health sweep and all four flows authenticate as **`ci_smoke`, which must not be a
  superuser.**
- The sweep **never requests any path under `/seek/admin/`**, at any privilege level.

`ci_write` exists only for `@pytest.mark.write`, which is deselected by default. It has to be
a superuser because the attributes and assay-registration endpoints are gated on
`IsSuperUser`, and those are precisely the endpoints whose defects reached production.

### 2.2 Both accounts must log in through the browser once, by hand

`BasicAuthentication` validates against Django's `auth_user` table, and that row is only
created or refreshed by `dmac/views.py:52-108` during a `/login/` POST. An account that has
never logged in through the UI cannot authenticate to the API at all.

So account setup is: create in SEEK, then log in once at `/login/` on each box. Until that
happens every API assertion fails with 401 and the cause is not obvious.

---

## 3. Layout

```
.github/workflows/ci-pytest.yml     job 1, GitHub-hosted, informational
.github/workflows/ci-smoke.yml      jobs 2-4, self-hosted on fairdata-dev
ci/pytest-baseline.txt              301 known-failing IDs + the command that made them
ci/smoke/
  conftest.py                       readiness gate, credentials, authenticated fixtures
  test_health.py                    HTTP sweep
  test_flows.py                     the four browser flows
  test_write_lane.py                opt-in, @pytest.mark.write
  fixtures/                         generated at setup, not committed
  README.md                         how to run it locally
```

---

## 4. Job 1: pytest, GitHub-hosted, informational

```yaml
runs-on: ubuntu-latest
steps:
  - checkout
  - astral-sh/setup-uv, cache enabled
  - uv sync
  - uv run baml-cli generate --from ./dmac_assistant/baml_src --no-version-check
  - run the suite (continue-on-error: true)
  - extract, diff, render the job summary
```

The run, verbatim:

```bash
DJANGO_SETTINGS_MODULE=dmac.test_settings PYTHONDONTWRITEBYTECODE=1 \
uv run pytest nextseek_api seek chat_nextseek startup build_tools scripts \
  --continue-on-collection-errors -q
```

Rules:

- **Never pass `-p no:logging`.** It removes the plugin providing `caplog` and manufactures
  `fixture 'caplog' not found` errors.
- `PYTHONDONTWRITEBYTECODE=1` is set because this repo has already been bitten by stale
  `.pyc` files producing untrustworthy results.
- The `baml-cli generate` step is required. Its output is gitignored, so a fresh checkout
  does not have it.

The job summary renders three sections and **always exits 0**:

- **NEW failures**, present in the run and absent from the baseline, in bold at the top
- **Fixed**, present in the baseline and absent from the run, as a nudge to shrink it
- Totals, for reference only, labelled as unreliable for comparison

`pytest-timeout` is not installed. Either add it and set a per-test timeout, or accept that a
hung test burns the job's wall clock. Recommend adding it; a CI job with no per-test timeout
eventually wedges.

---

## 5. Job 2: readiness gate, self-hosted

A blind `sleep 300` fails two ways: it wastes four minutes when the stack is up in ninety
seconds, and it still reports green if the stack comes up and dies at 5m01s.

```
floor      300s      nothing is checked before this (operator's requirement, configurable)
poll       every 10s thereafter
ready when 3 consecutive successes, roughly 30s apart:
             GET /login/                              -> 200   nginx + Django alive
             GET /nextseek_api/people/current/ (auth) -> 200   database AND SEEK reachable
             container state                                  not restarting
ceiling    600s -> fail, reporting the LAST STATUS SEEN, never a bare timeout
```

Two things about the second probe.

**It must be authenticated.** `/nextseek_api/` returns 401 to an anonymous caller. That is
honest, but a 401 proves only that Django and DRF are alive. There is no `CACHES` block in
`dmac/settings.py`, so sessions fall back to Django's database backend, which means an
authenticated 200 is the cheapest thing that actually proves MySQL is reachable.

**`/nextseek_api/people/current/` is the right probe** because it also proves SEEK Rails is
answering, and because its body identifies the caller. Run it first and use its result to
diagnose every downstream 401 or 502.

**Implementation note.** nginx answers 502 **instantly**, so a naive retry loop burns every
attempt in about two seconds. Use `curl --retry N --retry-delay 5 --retry-all-errors`, or an
explicit sleep inside the loop.

---

## 6. Job 3: HTTP health sweep

Runs as `ci_smoke`. Seconds, not minutes.

### 6.1 Two authentication modes, and they are not interchangeable

This was the biggest correction that came out of building the suite. The design assumed one
authenticated client. There are two, and using the wrong one produces a sweep that reports
everything healthy.

| surface | how | verified |
|---|---|---|
| `/nextseek_api/*` | HTTP Basic | 200 across the whole API sweep |
| `/seek/*` | a real session cookie from a POST to `/login/` | 200 on all six pages |

Basic auth **does not work for `/seek/` pages.** Measured: all five of the pages tried
returned a 302 to `/login/` while holding valid Basic credentials, because those views read
`request.session['username']`, which Basic auth never populates. The same pages return 200
once a real login POST has set a `sessionid`.

That is why `allow_redirects=False` is not a detail. Followed, every one of those 302s
reports 200, because that is the status of the login page.

```python
api = requests.Session()               # /nextseek_api/*  Basic, cookie-free
api.auth = (CI_SMOKE_USER, CI_SMOKE_PASS)

web = requests.Session()               # /seek/*  real login POST
web.get(f"{base}/login/")              # sets csrftoken
web.post(f"{base}/login/", data={..., "csrfmiddlewaretoken": token},
         headers={"Referer": base}, allow_redirects=False)   # expect 302
```

Four traps:

1. The Django `auth_user` row must already exist. Confirmed live: the seeded non-superuser
   returned 401 to Basic auth until a single `/login/` POST was performed, after which it
   returned 200. See 2.2.
2. **A `sessionid` cookie silently outranks an explicit `Authorization: Basic` header**,
   because DRF stops at the first authenticator that succeeds and session auth sits above
   Basic in most viewsets. The two clients must never share a session.
3. `/nextseek_api/attributes/*` re-proves the caller against SEEK on **every** request. If
   SEEK is down, even the read-only search returns 401 rather than a 5xx.
4. A failed login returns **200**, re-rendering the login form, not a 4xx. Assert the 302.

There is no reachable token-issuing endpoint. `dj_rest_auth` is mounted only in
`api_app/urls.py`, whose `include()` is commented out at `dmac/urls.py:28`. A DRF token can
only be minted from a shell.

### 6.2 What the sweep asserts

Three assertions per URL, not one:

- a sane status, and specifically **not a dead gateway** (see below)
- **not a silent redirect to `/login/` while authenticated**
- for JSON endpoints, a shape assertion, because a great many of them return 200 on failure

**"Not 502" turned out to be the wrong rule.** The first run found `entity_tree/nodes/`
returning a 502 that is not an outage at all: it is a JSON envelope reporting that 34 sample
types have no attribute definitions, so the endpoint refuses to emit `metadata_fields`. A
blanket rule paints that permanently red and the whole sweep gets ignored.

The discriminator is the body, and it is reliable:

| 502 shape | meaning | verdict |
|---|---|---|
| HTML | nginx, gunicorn is not answering | dead stack, always fail |
| JSON `errors` envelope | the application, a data or upstream condition | report, treat as a known condition |

Known conditions are recorded as `xfail` with the measurement in the reason, so they flip to
XPASS when fixed instead of quietly passing. Two exist today: this one, and the identity
issue in 6.7.

### 6.3 `allow_redirects=False` is mandatory

Seventeen `seek` views return a redirect to `/login/` when the session check fails. With
`requests`' default `allow_redirects=True`, every one of them reports **200**, because that is
the status of the login page, and the sweep is worthless. Assert the redirect status and the
`Location` header.

One live example of why this matters: `/seek/samples/upload/` redirects to
`/login/?next=/seek/samples/batchupload/`. The `next` path is not the requested path. Do not
assert on it.

### 6.4 The sweep set

Highest value first.

| Path | Assertion |
|---|---|
| `GET /nextseek_api/schema/` | 200, parses as OpenAPI, `len(paths)` matches a pinned number. **The single highest-value check in the suite**: drf-spectacular returns 500 if any `@extend_schema` in the codebase is malformed, so this one request validates every annotated endpoint at once. |
| `GET /nextseek_api/` | Body has exactly 15 keys. A 16th or a missing one means a router registration changed. |
| `GET /nextseek_api/people/current/` | 200, `data.attributes.login` equals `ci_smoke`. The identity probe. |
| `GET /nextseek_api/entity_tree/edges/` | 200 with non-zero `count`. Cheapest liveness proof for neo4j. |
| `GET /nextseek_api/entity_tree/edge_attributes/` | At least one edge has a non-null `internal_assay_id`. All-null means the MySQL enrichment failed silently behind a 200. |
| `GET /nextseek_api/assistant/me/` | 200, `is_admin` reflects `is_superuser` only. Regression guard: login sets `is_staff=1` on every SEEK user, so `is_staff` admits everyone and `IsAdminUser` is meaningless here. |
| `GET /nextseek_api/{sops,data_files,projects,people,investigations,studies,assays,sample_types}/` | 200, JSON:API envelope with a `data` list. A 502 here is honest and means SEEK is down. |
| `GET /nextseek_api/attributes/` | 200, `attributes` list plus a `pagination` object. |
| `GET /nextseek_api/entity_tree/nodes/` | Envelope is doubly wrapped: assert `results.total`, not a top-level `total`. |
| `GET /login/` | 200, sets `csrftoken`, body contains `name="username"`. |
| `GET /admin/login/` | 200. Cheapest proof Django itself is serving. |
| `GET /seek/help/` | 200 anonymous. The only genuinely public, genuinely 200 page in `seek/`. |
| `GET /static/<a collected asset>` | 200. Catches a missed `collectstatic`. |
| the 17 redirect probes | 302 with the expected `Location` prefix, `allow_redirects=False` |

### 6.5 Excluded from the sweep, permanently

- **Everything under `/seek/admin/`.** See 2.1.
- **`/logout`.** It fails unconditionally and does destructive work before failing. Never
  probe it from a fixture.
- **Every endpoint that calls a model.** The assistant and cc-assistant query routes each
  cost real money; one of them always spawns an ephemeral Claude Code container on Opus.
- **Anything requiring Luria SSH.**
- **Six `seek` routes that return 500 on a bare GET.** These are pre-existing defects, filed
  separately, not things CI should assert on until they are fixed.
- **A handful of dead routes** that cannot work at all, including the whole `/api/` tree,
  whose `include()` is commented out.

### 6.6 What a 200 does not prove

This section exists so nobody later mistakes a green sweep for a healthy system.

- **`GET /` returns 200 with a completely broken database.** Its three queries are each in
  their own `try/except` and the tiles default to zero. Assert `li.dash-row-item` count is
  greater than zero, not the status.
- **`POST /nextseek_api/schema_rag/retrieve/` returns 200 unconditionally.** Every failure is
  encoded in `body["debug"]["error_code"]`.
- A SEEK outage becomes `total: 0` on several endpoints, which is indistinguishable from a
  genuinely empty result.
- Roughly thirty `seek` paths return permission denials, not-logged-in states, and
  wrong-method errors as **HTTP 200** with a message in the body.
- The async 202 routes swallow the real failure in a worker thread. No HTTP status ever
  reflects it.

### 6.7 The suite found a real defect on its first run

`test_seek_identity_matches_the_authenticated_caller` asserts the obvious thing: the API
should report the caller's own SEEK identity. It does not. Two different accounts, each with
correct credentials, are both reported as the same person.

It is checked in as `xfail` with the measurement in the reason, so it flips to XPASS the day
it is fixed. The mechanism, the proof and the fix are in the uncommitted findings note, since
this document is public.

Two things follow for the CI design itself.

**Identity assertions belong in the sweep.** The original design had the identity probe only
as a liveness check. Asserting *who* comes back, rather than that something comes back, is
what turned a passing probe into a finding. Wherever an endpoint reports identity or scope,
assert the value, not the status.

**This is the argument for the whole exercise.** The defect is invisible to the existing test
suite because it only appears across two sequential requests by different callers against a
shared process. Nothing that runs inside one test transaction can see it.

---

## 7. Job 4: functional flows, Playwright

Runs as `ci_smoke`, on the runner host, against nginx.

### 7.1 Ground rules

- **Point at nginx** (`http://127.0.0.1:8000` locally), never at gunicorn. Django serves
  nothing under `/static/`; only `^media/` is wired. Aim at the wrong port and every asset
  404s.
- **Viewport width must be at least 768px.** `searchAdvanced.html` renders two separate UIs
  into one document and hides the desktop one below that breakpoint, so every EasyUI selector
  becomes `display:none`.
- **There are no iframes.** Every `*.embed.html` is a server-side `{% include %}` into the
  same document. `frameLocator` is needed nowhere. The `.embed` suffix is a legacy name from
  an older design. The one exception that behaves like a frame boundary is the EasyUI combobox
  dropdown, which is appended to `<body>`, so scope combo-item selectors to `body`.
- **Log in through the real form.** Session keys are written only by the login view, so
  `force_login` and injected cookies do not work. Do it once and reuse a shared
  `storageState`.
- **Do not set a Basic-Auth header on the browser context.** It leaks onto CDN font requests
  and breaks the bundle. Session cookie only.
- **Gate on initialization, never on a sleep**, and wait for `div.window-mask` to be hidden
  before any post-action click:

```js
await page.waitForFunction(() => window.jQuery && !!jQuery('#advanced_dgtable').data('datagrid'));
await page.waitForFunction(() => window.jQuery && ($('#validate_project_id').combobox('getData')||[]).length > 0);
```

### 7.2 The console-error check

This is the reason job 4 exists. It is also the thing most likely to be switched off within a
week if it is turned on carelessly.

**Order of operations:**

1. Swap `base.html:32` and `:33` first (section 1.6). Removes the universal `TypeError`.
2. Allowlist or vendor the four external CDNs: `fonts.googleapis.com`, `fonts.gstatic.com`,
   `cdn.jsdelivr.net`, and `cdn.skypack.dev` if the lineage tree is tested. Chromium reports
   blocked resources as console messages of type `error`, so an offline runner sees four to
   eight per page before any application code runs. Worse, `nextseek.js:31` calls into
   bootstrap, so a blocked jsdelivr makes every sidebar click throw.
3. Exclude `/seek/sample_timeline/` from the check, or rebuild that bundle with the correct
   Vite `base`. It emits five `/assets/js/*` 404s that cannot be fixed from the template.
4. Only then turn the check on, failing on any uncaught error not in the allowlist.

Two per-page items need runtime confirmation before the allowlist is final: one project-avatar
404 on the projects pages, and a probable inline-JSON error on `/seek/admin/clades/` caused by
Python `repr` being emitted where JSON is expected. Confirm both against the real environment
rather than trusting the static analysis.

**A console check cannot catch everything.** The chat frontend's template tag returns an empty
string in production when its manifest or entry key is missing: no exception, no console
error, an empty div at HTTP 200. Assert the script tag explicitly.

### 7.3 Flow A: advanced search

`GET /seek/search/?tab=advanced`.

Query: **`Uterus`, PARTIAL, no sample type, 38 rows** against the seed. Chosen deliberately.
The term must appear in a metadata **value**, not a key: the SQL `LIKE` matches keys too, but
a row is dropped unless the highlighter produced a non-empty `attributeValue`. A term that
matches only an attribute name returns zero rows despite a SQL hit, which is the main way to
author a brittle query on this page. `Lung` matches 3795 rows and is too slow; there is no
server-side LIMIT.

**`#input_searchText` is an EasyUI multiline textbox, not a plain input.** On init EasyUI
hides it and injects a real `<textarea>` into a sibling span, so `fill()` on the id times out.
Drive the real UI path instead: type into `#input_searchValue` (which *is* a plain input) and
click Add.

Assertions:

- `#numberSamplesFound` parses to greater than zero
- `page.evaluate(() => $('#advanced_dgtable').datagrid('getRows').length)` equals it
- first row's UID cell links to `/seek/sample/id=<digits>/`
- first row's `attributeValue` contains the term

Assert on `count()` and `textContent`, never `toBeVisible()`: the grid is built while its tab
is hidden, and the two mitigations in the code are load-bearing. Never select `#sample_table_div`
on this page; it is defined twice. Sample type ids are deployment-specific, so read them from
`window.type_options` rather than hard-coding.

### 7.4 Flow B: Nessie

`GET /seek/assistant/?q=playwright%20smoke`.

Sending a message costs real money. The cheapest proof the page is wired exploits a real
feature rather than a mock: the input hydrates from the `?q=` parameter on mount, so

```js
await expect(root.getByTestId('chat-input')).toHaveValue('playwright smoke');
```

is something only live JavaScript can produce, and it covers the silent empty-bundle failure
that a console check cannot see. Also assert the bundle's script tag is present.

To prove the outbound request without paying, register an abort route on
`**/nextseek_api/cc-assistant/query/async/` first, then assert on the intercepted request
body. Match on **pathname**, not a substring: `assistant/query/async` also matches the legacy
route.

The send button is correctly disabled on empty input, so "disabled on load" is not a failure.

### 7.5 Flow C: sample page

`id=` is part of the path segment, not a query string, and matches digits only. **Discover the
id at test time** from `POST /nextseek_api/samples/advanced_search/` and skip the test if the
database is empty. Do not hard-code.

Assert `table.TFtable tr` count is greater than zero and that the UID row matches the UID that
was searched for. Assert `response.status()` separately, so a SEEK Rails failure is
distinguishable from a UI regression.

Put lineage-tree assertions behind an opt-in flag: the tree needs both an external CDN and
neo4j, and neo4j being down is a known condition here. Smoke-test the attribute table
unconditionally.

Note for negative tests: a bogus-but-numeric id returns **200 with an empty table**, not 404.
A bogus UID returns 500. Do not write a test expecting 404.

### 7.6 Flow D: upload

`GET /seek/samples/upload/`, then `POST /nextseek_api/batch-upload/validate/`.

Give the page load a generous timeout. The view makes one SEEK call per institution and one
per person before rendering.

The file input is a plain native input, so `setInputFiles` works directly. Its `name` attribute
is a red herring; the JavaScript builds a fresh `FormData` and appends under the key `file`.

**Read the log with `inputValue()`, not `textContent()`.** `#messages` is a `<textarea>` and
is written via `.value`, so `textContent` returns the original whitespace and never matches.

**Validation is free**: no LLM, no Celery, no INSERT. It runs the pipeline through TRANSFORM
and stops. It returns **200 even for an invalid sheet**; validity lives in the body's `valid`
flag. Never infer validity from `response.ok`.

**Fixtures.** The two `wave3_*.xlsx` files in the repo have the right shape but reference a
sample type and ids that exist only in another test's database fixture, so they are **negative**
fixtures. Use one for the `FAILED` path, and **generate** the positive fixture at setup from a
sample type read at runtime from `/nextseek_api/sample_types/`.

**Never click the second submit button.** `form="sample_upload"` triggers a real Celery job
that writes to MySQL and neo4j.

Two free guard-rail tests with no round trip: click Validate with no file, and with a file but
no project, asserting the two client-side guard strings.

---

## 8. Two operational hazards on a shared box

Both are consequences of running CI against fairdata-dev while humans use it.

**Validate takes a MySQL advisory lock.** UID generation always runs, is not gated by the
`checks` parameter, and takes `GET_LOCK('uid_gen:<prefix>', 10)`. A CI validate running
concurrently with a human's real upload contends for up to ten seconds per prefix. Not
dangerous, but it is a real interaction, and it means flow D is not free of side effects even
though it writes nothing.

**Module-level mutable state leaks across requests.** Four `seek` views write into a
module-global dict that persists across requests and users. Do not run flow D concurrently
with another authenticated session and then assert on lab or creator contents.

---

## 9. A limit worth stating plainly

nginx has no `client_max_body_size` anywhere, in the checked-in config or the running
container, so the **1 MB default** applies. Django is configured for 200 MB. Any realistic
`.xlsx` over 1 MB gets a 413 from nginx and never reaches the validate view.

This is not a CI problem to solve, but it bounds what flow D can ever test, and it is
presumably a live user-facing limit that nobody has written down.

---

## 10. Prerequisites (operator)

1. **A self-hosted runner on fairdata-dev.** You install it; I write the workflow and the
   install steps.
2. **Two accounts, `ci_smoke` (not a superuser) and `ci_write` (superuser).** Each must log in
   once through `/login/` on each box before anything works. See 2.2.
3. **`~/.config/nextseek/ci.env` at mode 600** on the workstation and on fairdata-dev.
4. **Decide whether `startup.sh` may call `gh workflow run` on success.** Still open. The hook
   point is `startup/cli.py:566`, immediately after `ui.ok(f"{policy.name} rebuilt and
   restarted")`. Note `startup.sh` is an 18-line wrapper; the logic is in the Python CLI.

---

## 11. Sequencing

1. Swap `base.html:32`/`:33`. One line, no dependencies, makes step 5 tractable.
2. Commit `ci/pytest-baseline.txt` and `ci-pytest.yml`. Fully specified and already proven;
   nothing blocks it.
3. Build `ci/smoke/` as a plain pytest suite. Prove it locally against `127.0.0.1:8000`.
4. Run the same command by hand on fairdata-dev. Prove it against the real stack.
5. Turn on the console-error check, after the CDN allowlist is confirmed at runtime.
6. Wrap steps 3 to 5 in `ci-smoke.yml` once the runner is installed.
7. Add the write lane last.

Steps 3 and 4 come before any workflow YAML because the smoke files must run as a plain
command anyway. Nothing is thrown away.

---

## 12. Non-goals

- Deploying anything. CI observes; the operator rebuilds.
- Fixing the 301 baseline entries. Shrinking the baseline is separate work.
- Running the evidence lane that is bound to its author's machine.
- Fixing the pre-existing 500s and dead routes found during this review. They are filed
  separately.
- Building on `startup/`'s existing test lanes. One is honest but pinned to an image digest
  that is not present here, one fails at import, and `./startup.sh doctor` reports success
  when SEEK's database is unreachable and when the Bedrock token is empty. None is a
  foundation.

---

## 13. Status: built and proven locally

Everything in sections 3 to 7 is implemented and passing against the local stack.

```
39 passed, 5 skipped, 2 xfailed in 13.7s      ci/smoke/
 4 passed, 1 skipped                          ci/smoke/ -m write
```

The 5 default skips are the write lane, correctly deselected. The 2 xfails are 6.2 and 6.7.
The write lane's remaining skip is the real INSERT, behind a second opt-in
(`CI_WRITE_DESTRUCTIVE=1`) because creating an attribute renumbers positions in a way that
deleting it does not undo.

The baseline differ is validated both ways: a run diffed against its own baseline reports no
new failures, and diffing the clean-sync run against the image-venv run reproduces exactly
the 4-new / 10-fixed split found independently. Baseline and differ share one parser, via
`--emit-baseline`, so they cannot drift.

The `base.html` ordering fix is made and proven against the live assets without a rebuild, by
rewriting the two tags in flight and comparing:

| | uncaught errors | `enableFilter` installed |
|---|---|---|
| as served | 1 | **no** |
| swapped | **0** | **yes** |

Note the second column. On a page that does not manually re-include the file, the filter API
is genuinely absent, not merely late, so this is not a cosmetic fix even today. The suite
observed the error on all six flows before the fix.

### Corrections to earlier claims in this document

- Section 1.5 said the workstation had Playwright browsers. That check was too weak: the
  cache directory existed but held nothing this environment could use, and `playwright
  install chromium` was needed. The one-time host step is real, not hypothetical.
- Section 7.3's "38 rows" for the `Uterus` query comes from the packaged seed. The local
  stack returns 355. Row counts are environment-specific: assert greater than zero, and
  against the page's own reported total, never a literal.
- The search grid paginates at 100, so the rendered row count is one page and does not equal
  the reported total.

### Still to do

Run the same command against fairdata-dev, then wrap it in `ci-smoke.yml` once the runner is
installed. The floor of 300s in the readiness gate is a guess carried from the design and
should come down once there are real numbers from the box.

---

## 14. What CI still will not catch

Production's database is charset-drifted in a way no freshly built environment reproduces:
558 utf8mb3 columns and zero utf8mb4, under a utf8mb4 connection, where local dev has the same
columns as utf8mb4 throughout. Code that assumes column charset equals connection charset
passes every local check and every CI check, and fails only on production. That is exactly how
the mutation defect reached production and stayed there.

This suite reduces the class of bug that reaches production. It does not close that gap, and
issue #120 tracks the cure.
