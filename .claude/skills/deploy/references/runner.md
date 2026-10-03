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

`runner` refuses unless the preflight verdict is ok, the preflight holds the full 40-character
expected sha, and (when the brief left `images` null) the commits step found no stop. `ssh
--purpose start` also refuses a preflight taken more than 90 minutes before the start
(`PREFLIGHT_MAX_AGE_MIN` in `rules.py`): the box moves, so run the preflight again and re-render. It copies each
cases file into `$D/runner/launch-<TAG>-cases-<n>.json`, fills the template, and runs `bash -n` on
both scripts. `ssh --purpose start` copies `$D/runner/launch-<TAG>-*` to the box (`/tmp` on dev,
`<home>` on prod), makes them readable, and starts tmux session `launch-<TAG>`
unless one exists (`SESSION_EXISTS`). It prints the first status lines.

Before starting, announce one line: what, why, cost ("free" when there is no Nessie, else the
estimate from `brief.json` against the budget), expected minutes (`derived.estimate.minutes`).

## What the rendered runner does, in order

| Step | What | Stops the run when |
|---|---|---|
| window | checks the UTC clock against the stop windows | inside one |
| busy | `pgrep` inside `nextseek` for a graph sync, drift or harness run | anything found |
| pull | `git fetch`; discards only the app's context refresh; `git merge --ff-only <expected>` | origin/dev is not the sha, or the fast-forward is refused |
| rebuilds | each component in the order bedrock-proxy, app, cc-agent, nextseek-sidecar, `--no-ci` | the success line is missing (unless the component is in `continue_on_failure`: then it says `FAILED ..., continuing`) |
| after app | collectstatic; on dev the labs copy and a `nextseek` restart; `mkdir -p /app/runs` | never |
| judge | every red stack-health line of each rebuild log, classified | an unexplained or stale red while components are still pending; after the last rebuild it becomes a `FINDING` line and the run goes on to CI |
| checks | http, containers, boot markers, CC runner, labs, refresh marker, the ops road and, on the sidecar road only, sidecar ops against the repo, image ages, cc-agent node and Claude Code versions (when rebuilt), every live marker, memory | never (it is evidence) |
| CI | `./startup.sh ci [--wait-ready] --no-nessie` | never; it records the result line, new reds and red health lines |
| Nessie | only when CI printed a result and every failed id is a known red; restarts SEEK over 12 GiB when allowed; one run dir per cases file, copied out to `~/launch-<TAG>/` and `~/backups/` | CI not green apart from the known reds |

The red classes come from `.claude/skills/deploy/scripts/rules.py`: `known` (the table of known reds), `pending <c>` (a
check that names a component still to be rebuilt: expected mid-launch, since stack health runs after
every rebuild), `stale <c>` (it names a component already rebuilt: a real problem), `unexplained`.
The same table drives `launch.py judge` on the pulled logs; a test pins that the bash and Python
judges classify the same log identically.

## The status file

Every line ends in a UTC `HH:MM:SS`; the last is `ALL_DONE`. `launch.py judge` parses them:

```
START tag=... instance=dev components=app cc-agent
HEAD 1c070c08 Merge ...
REBUILD app exit=1               # exit 1 is often drift or GHCR; the success line decides
ROLLBACK app nextseek-nextseek:pre-<utc>-<sha>   # the NEW image was built from <sha>
REBUILT app | FAILED cc-agent, continuing (...)
STATIC exit=0 ...
LABS copied | LABS source missing: /tmp/labs_db.json
RED app [pending cc-agent] cc-agent runtime: STALE: ...
FINDING red after the last rebuild (...)
CHECKS CHECK http 200
CI exit=1
CI_RESULT CI failed: 2 failed, 304 passed, ...
CI_NEW_REDS <ids>
NESSIE_START run=... utc=...  /  NESSIE_EXIT run=... exit=1 utc=...
STOPPED: <why>
ALL_DONE
```

`NESSIE_EXIT exit=1` means at least one non-known_fail case failed; it is not an infrastructure
failure. The `utc=` stamps are the `--since/--until` for `fetch_run.py` (the app clock is UTC).

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
