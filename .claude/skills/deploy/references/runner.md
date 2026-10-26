# The runner, the watcher, the pull

One runner script per launch, run in tmux ON the box, strictly sequential, writing one line per
step to a status file. One watcher from the workstation over ONE ssh connection. This is the shape
every successful dev and prod launch of 2026-09-22 to 09-25 used; the difference now is that
`launch.py runner` renders it from the validated brief, so no one fills a template by hand.

## Render and start

```bash
uv run $K/launch.py runner --brief $D/brief.json          # -> $D/runner/launch-<TAG>-run.sh, watch.sh, runner.json
uv run $K/launch.py ssh --brief $D/brief.json --purpose start
```

The short form does both after the review in one command: `start --brief $D/brief.json --form
$D/commit-review-form.json` (SKILL.md, "The short form"). Its watch, pull and judge are one more:
`finish --brief $D/brief.json`, run in the background.

`runner` refuses unless the preflight verdict is ok, the preflight holds the full 40-character
expected sha, and (when the brief left `images` null) the commits step found no stop. `ssh
--purpose start` also refuses a preflight taken more than 90 minutes before the start
(`PREFLIGHT_MAX_AGE_MIN` in `rules.py`): the box moves, so run the preflight again and re-render. It copies each
cases file into `$D/runner/launch-<TAG>-cases-<n>.json` (`-cases-<n>-member.json` for a member file), fills the template, and runs `bash -n` on
both scripts. `ssh --purpose start` copies `$D/runner/launch-<TAG>-*` to the box (`/tmp` on dev,
`<home>` on prod), makes them readable, and starts tmux session `launch-<TAG>`
unless one exists (`SESSION_EXISTS`). It prints the first status lines.

Before starting, announce one line: what, why, cost ("free" when there is no Nessie, else the
estimate from `brief.json` against the budget), expected minutes (`derived.estimate.minutes`).
`prepare` prints that line ready to say (`ANNOUNCE`).

## What the rendered runner does, in order

| Step | What | Stops the run when |
|---|---|---|
| window | checks the UTC clock against the stop windows | inside one |
| busy | `pgrep` inside `nextseek` for a graph sync, drift or harness run | anything found |
| pull | `git fetch`; discards only the app's context refresh; `git merge --ff-only <expected>` | origin/dev is not the sha, or the fast-forward is refused |
| rehearsal | only when the range has a migration and the app is rebuilt: `app-build` goes first (the switchover order already has it) and, right after it, `migrate` runs on a throwaway copy of the box schema (DEPLOYMENT.md section 3.6) | the rehearsal fails, or `app-build` has no success line (even with the app in `continue_on_failure`): nothing has restarted yet |
| rebuilds | each component in the order bedrock-proxy, cc-spawner, app, cc-agent, nextseek-sidecar (on the phase 4 switchover: `app-build` (the app image built and preflighted with `--no-restart`, log `app-build.log`; its ROLLBACK line is the app's, later app tags are not reported), cc-agent, nextseek-sidecar, bedrock-proxy, cc-spawner, app (a cached build, then the restart), then `./startup.sh fix-ownership --check`, which stops the launch on any count above 0; a switchover brief without cc-agent and nextseek-sidecar stops before any rebuild), `--no-ci` | the success line is missing (unless the component is in `continue_on_failure`: then it says `FAILED ..., continuing`) |
| first split | right after the pull the runner asks the box, not the brief: `FIRST_SPLIT=1` when the checkout defines `web` and `docker compose ps -a -q web` lists nothing (a stopped or restarting `web` is a split box); the order stays the rules order. On `FIRST_SPLIT=1` the rebuild's summary "The app or front door is not up" and the new-layout reds (`web` alone not running, `cc-spawner` not running, its image ABSENT) are `pending app` / `pending cc-spawner` until their step has run, then real; anywhere else that summary stops. Why: the new nginx conf proxies to `web`, which only the app step creates, and the front-door start (607031e7) leaves nginx on the old conf until then. An nginx service definition that differs from the running one (compose config hash) moves the nginx recreate onto the app step (else the first) when the brief allows `nginx recreate ok`, else it is a `FINDING` | compose cannot list the `web` containers; on `FIRST_SPLIT=1`: the app step does not carry the nginx recreate, or the shipped sha lacks `no_recreate=True` in `startup/cli.py` (both also stop the commits step when the range creates `web`) |
| after app | collectstatic in `web`; on dev the labs copy (`compose cp -a`, so the file is owned by the app user) onto the volume `web` and `nextseek` share (no restart); `mkdir -p /app/runs` in both | never |
| judge | every red stack-health line of each rebuild log, classified | an unexplained or stale red while components are still pending; after the last rebuild it becomes a `FINDING` line and the run goes on to CI |
| checks | http, containers, (non-root builds) web uid 10001, no docker.sock in web, an untokened spawner call refused, User/CapDrop/no-new-privileges of web and nextseek, boot markers, CC runner, labs, refresh marker, the ops road and, on the sidecar road only, sidecar ops against the repo, image ages, cc-agent node and Claude Code versions (when rebuilt), every live marker, memory | never (it is evidence) |
| CI | `./startup.sh ci [--wait-ready] --no-nessie` | never; it records the result line, new reds and red health lines |
| Nessie | only when CI printed a result and every failed id is a known red; restarts SEEK over 12 GiB when allowed; one run dir per cases file (a `*-member.json` file as the non-admin `CI_SMOKE_USER`, every other as `CI_WRITE_USER`; a file with a non-empty `_measure` block is re-pinned on the box first, `PINNED`, and a failed pin skips it with `FINDING PIN_FAILED`), copied out to `~/launch-<TAG>/` and `~/backups/` | CI not green apart from the known reds |

The red classes come from `.claude/skills/deploy/scripts/rules.py`: `known` (the table of known reds), `pending <c>` (a
check that names a component still to be rebuilt: expected mid-launch, since stack health runs after
every rebuild), `stale <c>` (it names a component already rebuilt: a real problem), `summary` (the
rebuild's closing `Rebuild finished but is red: ... No rollback is needed` line: it stops beside
an unexplained or stale red, or alone with no known or pending red to explain it, so a rebuild whose
other reds are all pending or known goes on), `unexplained`.
A red's indented detail lines are part of its text (the `graph sync health` problems are). The same
table drives `launch.py judge` on the pulled logs; a test pins that the bash and Python judges
classify the same log identically and stop on the same lines.

## The status file

Every line ends in a UTC `HH:MM:SS`; the last is `ALL_DONE`. `launch.py judge` parses them:

```
START tag=... instance=dev components=app cc-agent
HEAD 1c070c08 Merge ...
REBUILD app exit=1               # exit 1 is often drift or GHCR; the success line decides
ROLLBACK app nextseek-nextseek:pre-<utc>-<sha>   # the NEW image was built from <sha>; a component's first ROLLBACK line wins
MIGRATION in the range: app-build first, ...; order: app-build ...   # only when the range has a migration
REBUILD app-build exit=1         # the app image built with --no-restart (switchover or migration); the success line decides
REHEARSAL-OK applied=<names> seconds=<n>   # right after app-build: migrate on a copy of the box schema; a failure is STOPPED, nothing restarted
REBUILT app | FAILED cc-agent, continuing (...)
STATIC exit=0 ...
LABS pre-copied | LABS pre-copy failed; ...   # before the app rebuild; the last LABS line decides
LABS copied | LABS source missing: /tmp/labs_db.json
RED app [pending cc-agent] cc-agent runtime: STALE: ...
FINDING red after the last rebuild (...)
CHECKS CHECK http 200
CI exit=1
CI_RESULT CI failed: 2 failed, 304 passed, ...
CI_NEW_REDS <ids>
PINNED <file> measured={...} dropped=<ids>   # a cases file's non-empty _measure block re-pinned on this box
FINDING PIN_FAILED <file> see <launch>/pin-<file>.log; this file's run was skipped
NESSIE_START run=... utc=... login=admin|member  /  NESSIE_EXIT run=... exit=1 utc=...
STOPPED: <why>
ALL_DONE
```

`NESSIE_EXIT exit=1` means at least one non-known_fail case failed; it is not an infrastructure
failure. A cases file whose pin failed has no NESSIE_START, so the Nessie section can hold fewer runs
than the brief has cases files: count them against the `FINDING PIN_FAILED` lines.
The `utc=` stamps are the `--since/--until` for `fetch_run.py` (the app clock is UTC).

## Watch

```bash
uv run $K/launch.py ssh --brief $D/brief.json --purpose watch --script $D/runner/watch.sh --out $D/watch.out
```

Run it with Bash `run_in_background: true` and wait for the completion notice. It is one connection
that loops ON the box (300 minutes at most) and prints the status, the rebuild lines, the checks, the
CI result and the harness lines when `ALL_DONE` appears. While it runs, `launch.py ssh --purpose
status` allows ONE short status read (the script counts it). If the status has not moved for 30
minutes, report it; do not kill anything.

## Pull

```bash
uv run $K/launch.py ssh --brief $D/brief.json --purpose pull     # -> $D/box.tgz, unpacked into $D
```

## The one-connection rule, as code

`launch.py ssh` is the only door. It builds the transport per instance (dev: the whole remote
command as ONE argument, `echo <b64> | base64 -d | sudo -n -u <run_as> bash -l`, so the
quoting trap cannot happen; prod: direct key login), holds a lock per launch folder so a second
connection is refused, appends every connection to `$D/connections.jsonl`, and after one failed
connection (exit 255 or a timeout) refuses every later one with exit 7 until the operator clears it
(`--after-failure "<their words>"`). A timeout on port 22 is usually the VPN or a struggling
box: check the front door over HTTPS, then report.

A read-only script of your own (a re-pin batch) goes through the same door:
`ssh --brief $D/brief.json --purpose read --script $S/pin.sh --out $D/pin.out`.
