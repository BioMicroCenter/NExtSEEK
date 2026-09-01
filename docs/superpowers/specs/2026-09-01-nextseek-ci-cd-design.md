# NExtSEEK CI/CD: pytest reporting plus post-rebuild health and smoke tests

Date: 2026-09-01
Status: approved design, not yet implemented

## Problem

There is no CI in this repository. `.github/` holds only issue templates; there is no
`workflows/` directory and no other CI config anywhere in the tree.

Two consequences, both observed directly on 2026-08-31/09-01:

1. **Regressions are found by hand or not at all.** Three separate defects in the attributes
   mutation path reached production and sat there, each hidden behind the previous, because
   nothing exercised the endpoint end to end against a real database.
2. **A page can return HTTP 200 while being broken.** `/seek/samples/attributes/` throws
   `TypeError: Cannot read properties of undefined (reading 'methods')` from
   `datagrid-filter.js:10` on **every** load, and has for as long as anyone has looked. A
   status-code check calls that page healthy.

## Constraints that shaped the design

- **The suite is not green and will not be.** Measured on merged code: **12 failed, 1704
  passed, 363 errors**. The failures and errors are pre-existing and environmental -- the
  `/home/taishajo` evidence boundary that `scripts/attribute_api_test.sh` and
  `attribute_fixtures.py:16` hard-code, and an unreachable neo4j. A "must be green" gate
  fails on run one and is ignored by run five.
- **Counts lie; names do not.** A combined run once read "11 failed" where the pre/post
  failure NAME sets were identical -- collecting `assay_registration` alongside changes
  OpenAPI schema generation. Compare sorted test IDs, never totals.
- **`-p no:logging` manufactures errors.** It disables the plugin that provides `caplog`, so
  every test using that fixture errors with "fixture 'caplog' not found". It inflated the
  error count by 10 in earlier measurements. CI must not pass that flag.
- **The stack is heavy.** The app image alone is 8.7GB, plus SEEK/Rails, MySQL with a real
  seed, neo4j and solr. A GitHub-hosted runner cannot stand this up in reasonable time.
- **nginx serves 502 for 60-120s after a restart** while gunicorn comes up (45s measured
  locally, longer on the dev box). Any check that starts too early sees a false red; any
  fixed sleep that is too short sees a false red and one that is too long wastes every run.
- **The dev box is shared.** Humans and multiple Claude sessions work on fairdata-dev
  concurrently. CI must never restart that stack on its own initiative.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Runners | Hybrid: pytest on GitHub-hosted, health/smoke on self-hosted (fairdata-dev) | The stack only exists on the box; a red pytest job should not hide page health |
| Trigger | Operator rebuilds, then CI tests (`workflow_dispatch`, optionally called by `startup.sh` on success) | CI never restarts a shared box under someone else's work |
| Write policy | Tiered: read-only by default, opt-in write lane | Safe to run constantly; still able to prove INSERT on demand |
| Test driver | Both: HTTP sweep for health, Playwright for flows | 200 != working; the console-error check is the only thing that catches the class of bug the attributes page has |
| pytest gate | **Informational, never blocks** (operator's decision) | No false alarms. Mitigated below rather than overridden |

### Note on the pytest gate

The operator chose informational-only. Its weakness is that a real regression looks exactly
like the existing noise. The mitigation, which costs nothing and does not override the
decision: the job still diffs against a committed baseline and renders **new** failures
prominently in the GitHub job summary. It simply does not set a non-zero exit code. Signal
without blocking.

## Layout

```
.github/workflows/ci-pytest.yml     job 1, GitHub-hosted, informational
.github/workflows/ci-smoke.yml      jobs 2-4, self-hosted on fairdata-dev
ci/pytest-baseline.txt              known-failing test IDs, one per line
ci/smoke/
  conftest.py                       readiness gate, authenticated session fixture
  test_health.py                    HTTP sweep
  test_flows.py                     the four browser flows
  test_write_lane.py                opt-in, @pytest.mark.write
```

The smoke tests are pytest files, not shell, so the same command runs them locally and in
CI. That is what keeps them maintainable.

## Job 1 -- pytest (GitHub-hosted, informational)

Runs the unit lanes that need no stack. Collects `FAILED` and `ERROR` test IDs, sorts them,
diffs against `ci/pytest-baseline.txt`, and writes a job summary:

- **NEW failures** (in the run, absent from the baseline) -- rendered in bold at the top
- **Fixed** (in the baseline, absent from the run) -- a nudge to shrink the baseline
- Totals, for reference only

Always exits 0. Must NOT pass `-p no:logging`.

DB-backed lanes cannot run here -- there is no stack on a GitHub runner. They belong to job 2.

## Job 2 -- readiness gate (self-hosted)

The critical piece. A blind `sleep 300` fails two ways: it wastes four minutes when the stack
is up in 90 seconds, and it still goes green if the stack comes up and dies at 5m01s.

```
floor      300s    nothing is checked before this (operator's requirement)
poll       every 10s thereafter
ready when 3 consecutive successes, ~30s apart:
             GET /login/          -> 200   nginx + Django alive
             GET an API endpoint  -> 200   database reachable
             container state              not restarting
ceiling    600s -> fail, reporting the LAST STATUS SEEN, not a bare timeout
```

Sustained readiness, not a momentary one. The floor is configurable so it can come down once
there are real numbers from the box.

Practical note for whoever writes the poller: nginx answers 502 **instantly**, so a naive
retry loop burns all its attempts in about two seconds. Use a real delay --
`curl --retry N --retry-delay 5 --retry-all-errors`, or an explicit sleep in the loop.

## Job 3 -- HTTP health sweep (seconds)

Every relevant URL asserts: a sane status, **not** 502, and **not** a silent redirect to
`/login/` while authenticated. Enumerate the real list from `seek/urls.py` rather than
guessing. Known entries: `/`, `/login/`, `/seek/search/`, `/seek/projects`,
`/seek/samples/upload/`, `/seek/samples/attributes/`, the assistant endpoint, the API root.

## Job 4 -- functional flows (Playwright, 1-2 min)

- **advanced search** -- runs a search, results render
- **Nessie** -- loads and responds
- **sample page** -- opens a known sample, fields populate
- **upload** -- drives `POST /nextseek_api/batch-upload/validate/`, asserts real `totals`

Every flow **also fails on uncaught console errors**. That check is the reason this job
exists: it is what catches `datagrid-filter.js` and everything like it.

`validate` runs the pipeline through TRANSFORM and stops before INSERT with no database side
effects, so the upload flow writes nothing.

## Opt-in write lane

`@pytest.mark.write`, deselected by default, enabled by a workflow input. Inserts into a
CI-only project, asserts, and deletes in a fixture teardown so a mid-test failure still
cleans up.

## Prerequisites (operator)

1. **A self-hosted runner on fairdata-dev.** Install steps can be written; installing it is
   the operator's action.
2. **A dedicated CI login.** Preferably NOT a superuser, so a bug in the suite cannot mutate
   schema. Where the credentials live -- GitHub secret vs read from the box -- is a
   deliberate decision, not a default.
3. **Whether `startup.sh` may call `gh workflow run`** on success, or the trigger stays
   manual.

## Explicit non-goals

- Deploying anything. CI observes; the operator rebuilds.
- Fixing the 12 pre-existing failures or the 363 environmental errors. The baseline records
  them; shrinking it is separate work.
- Running the `/home/taishajo`-bound attributes evidence lane. It cannot run outside its
  author's machine and is tracked separately.
