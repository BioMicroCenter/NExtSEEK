# CI: running it and reading it

## The commands

| When | Command |
|---|---|
| Rebuilds | always `./startup.sh rebuild ... --no-ci`, then CI as its own step. The rebuild hook would add the paid Nessie lane on dev |
| CI right after an app rebuild | `./startup.sh ci --wait-ready --no-nessie` (a fixed 300 s readiness floor, then polling) |
| CI on a stack that has been up for a while | `./startup.sh ci --no-nessie` |

- `--no-nessie` always, unless the brief explicitly asks for the Nessie CI lane (three NS
  questions and one CC question; the CC turn alone has cost $0.44 to $0.54; local and dev only,
  prod never runs it). It is paid, so the paid rules apply.
- Never `-m "..."` to skip tests: any `-m` expression re-admits the write lane
  (`ci/smoke/conftest.py`).
- Never `--force-profile`: it widens what CI may write and needs a human at a prompt.
- CI runs `ci/` and `startup/` from the HOST checkout, so a pull that only touches those needs no
  rebuild before CI.

## Where the result is

| What | Where on the box |
|---|---|
| console (the runner's copy) | `~/launch-<TAG>/ci.log`, pulled to `$D/launch-<TAG>/ci.log` |
| last line | `CI passed: N passed, ...` or `CI failed: N failed, M passed, ...` |
| per-run record | `startup/ci-reports/*.md` (named for the image under test; accumulates) |
| junit | `startup/.ci-last-run.xml` (one slot, overwritten every run) |

Before the tests, CI prints stack-health lines: app and front door, `first-party images: all 4
present`, `cc services`, `cc-agent context: dmac-assistant:poc bakes all 6 canonical context files`,
and on dev `graph drift`. A `✗` there is a finding even when every test passes.

## Reading it

`launch.py judge` does the mechanical part: it takes the FAILED and ERROR ids from `ci.log`, marks
each known or new against the known-reds table in `.claude/skills/deploy/scripts/rules.py` (by test id: totals move every
time a branch adds tests, ids do not), and lists every red stack-health line at the top of CI. Your
part, in the report form: for each NEW red, its first `E ` line and whether the changed files could
plausibly cause it (`caused_by_change`: yes, no, unclear).

The stack-health lines print before the tests (app and front door, first-party images, cc services,
cc-agent context, graph drift on dev, and from rebuild 2 on: app image code, cc-agent runtime,
bedrock-proxy allow list, CC fallback wiring). `ci` exits on the suite only, so a red health line
is a finding even when every test passes; after the last rebuild every one must be green apart from
the known reds. The report refuses `shipped` while one is red.

## The graph sync health line

Every box built from this branch prints one more stack-health line, production included: `graph sync health:
<summary>`, from `manage.py graph_sync_health --json` in the app container. Green: nothing to fail on. Yellow
(`warnings:` under it): label changes awaiting approval, or `skipped` while the container is still migrating; not a
finding. Red, with one detail line per problem: an outbox row failing past its kind's back-off plus 30 minutes, the
latest full, reconcile, catalog or drift run failed, stale freshness, or dead rows. A red line makes `rebuild` exit 1
at its end and `ci` exit 1 after the suite, so it is a known red in one case only: on dev (any box but prod), when
its one problem line is a drift run that failed only `catalog.assistant_investigations` (operator ruling OP14,
"leave it red" on dev; `OFF_PROD_ALLOWED_DRIFT` in `rules.py`, the same set as `ci/smoke/test_graph_sync_status.py`).
Any other drift check, a stale job, a dead or overdue row or an overdue run still stops a launch, and on prod it
always does. Otherwise read its detail lines and report each as a finding. `graph_sync_health could not complete
(exit 3)` means the tables could not be read.

Between an app rebuild that moves the graph writer to a new schema version and the full sync that follows it, the
graph is still at the old version, so every graph write path refuses on purpose, drift prints `skipped` and `graph
small tables` warns: run the full sync straight after the rebuild, then `ci`. The full sync closes the rows enqueued
before it.

## Known reds

The table lives in `.claude/skills/deploy/scripts/rules.py` (`INSTANCES[...].known_reds`), each with the date it was last
true. As of 2026-09-25:

| Instance | Kind | Match | Why |
|---|---|---|---|
| dev | ci | `test_route_is_reachable[/seek/sample_types/id=...]` | SEEK's `SampleTypesController#show` spends about 22 s in the database, past the 20 s client timeout (D14) |
| dev | ci | `test_route_is_reachable[/nextseek_api/sample_types/...]` | same |
| dev | health | `graph drift: DRIFT: 1 of N checks failed: catalog.assistant_investigations` | dev lacks some investigations; alone it makes the rebuild exit 1 |
| dev, prod | health | `no usable GHCR credential` | no `~/.config/nextseek/ghcr.env` (issue #87); harmless |

Not in the table, on purpose, because each needs a reading: the two `3.catalog.*` drift checks after
a reconcile-only graph sync (open defect D7), prod's Playwright `page.goto /login/` timeouts right
after an app restart (propose a CI-only re-run), and `ci/smoke/test_graph_behaviour.py` write tests
on prod (a regression of 8cf1e935 if they reappear). A new known red goes into `rules.py` with a
test, never into a runner by hand.

Last known results: prod 279 passed, 72 skipped, 2 xfailed (b3d064f6, 2026-09-23). Dev 304 passed
plus the 2 SEEK reds (926be1e3, 2026-09-25).

## What a red does NOT mean

- The rebuild already happened. CI never rolls back and neither do you. Report the rollback
  tag.
- A red is not permission to fix anything on the box. Report it.
