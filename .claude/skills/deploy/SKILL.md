---
name: deploy
description: >-
  Use when deploying, redeploying, rolling back, verifying or launching the NExtSEEK stack: a
  greenfield install, shipping a code or config change to a running instance, rollback, post-
  deploy verification, or a launch ("rebuild it at dev", "deploy to prod") that takes one box to
  a commit already on origin/dev, rebuilds the named images, runs CI, asks and grades the Nessie
  questions and reports. Routes to DEPLOYMENT.md (the authoritative runbook), enforces the
  deployment-hygiene gates, and carries the launch scripts. Box hosts, accounts and paths come
  from a local boxes.json, never from git.
---

# NExtSEEK deploy

**Read the repo-root `DEPLOYMENT.md` in full before acting**: it is the
authoritative runbook and the stand-in for a CI/CD pipeline. This skill only
routes you and holds the hard gates.

## Standard verbs

- **App code**: `./startup.sh rebuild`: rebuild the shared app image and
  recreate `nextseek`, which runs every app-code process (web, both Celery
  workers, the outbox dispatcher, the recovery loop, the assay-registration drain).
- **CC image**: `./startup.sh rebuild --component cc-agent`, build only; the
  next chat turn uses it, with no persistent agent container to restart.
- **Sidecar / proxy**: use `--component nextseek-sidecar` or
  `--component bedrock-proxy`.
- **Every first-party image**: use `--component custom-stack`. This never
  rebuilds or restarts nginx, databases, SEEK, or Solr.
- **Dirty shared runtime checkout**: use `--source-tree <clean-origin-dev>`.
  Images build from that verified clean checkout while recreation continues
  from the installed instance, preserving its existing bind-mounted paths.

Every rebuild verb first creates and verifies local rollback tags, uses
`--no-deps --force-recreate` for long-running targets, gates each fresh image
for baked secrets, and attempts its component-specific private GHCR baseline.
The app rebuild reattaches the existing `attribute_mutation_broker` SQLite
named volume; it does not renew or delete it. Only the explicitly destructive
`reset` path drops volumes.
Local rollback failure aborts before building; GHCR failure remains non-fatal
but produces a loud banner and red doctor check. Do not bypass this with raw
`docker compose build` / `up`.
- **Diagnose**: `./startup.sh doctor` (read-only) before touching anything.
- **First-ever install on a box**: `./startup.sh install` (full pipeline:
  prereqs → config render → volumes → seeds → build → users → health).
- **Nuke and reinstall**: `./startup.sh reset` (DESTRUCTIVE: drops volumes).

## Route by task

| Task | Go to |
|---|---|
| Fresh install on a new box | DEPLOYMENT.md §2 (then NExtSTEPS.md before exposure) |
| Ship a code change to a running instance | DEPLOYMENT.md §3 (`./startup.sh rebuild`) |
| Config-only change (env / local_settings) | DEPLOYMENT.md §4 |
| Roll back a bad deploy | DEPLOYMENT.md §5 |
| Verify a deploy | DEPLOYMENT.md §6 (always, after every deploy) |
| Container-CC specifics / OI-3 checks | `NessieAI/cc/DEPLOY.md` + DEPLOYMENT.md §9 |

## Non-negotiable gates (apply to every deploy)

1. Deploy only committed code from `origin/dev` or `origin/main` (DEPLOYMENT.md §3:
   `dev` is the most up to date and `main` is synced from it); never `docker cp`
   fixes into a running container (ephemeral; lost on recreate).
   `--source-tree` refuses a dirty tree, a SHA other than `origin/dev`, a
   runtime/source SHA mismatch, or dirty runtime deployment-control files, so a
   `main` deploy can use it only while `main` and `dev` are the same commit.
2. Require the rebuild CLI's verified pre-tags. If raw image work is explicitly
   approved, create and inspect equivalent tags before replacing any image.
3. mysqldump gate before any deploy whose range includes a Django migration.
   Never `migrate --fake` a wedged migration.
4. Recreate only the selected component (`--no-deps`). The app component is a
   cohort: web + all processes that execute the shared app image.
5. Never weaken the CC agent isolation: zero shared credentials in the agent
   env, Bedrock only via the proxy, `nextseek` never joins `dmac-cc-net`.
6. Secrets exist only in the gitignored files (DEPLOYMENT.md §8), never in
   git, images, logs, or docs. Never push a `docker commit` snapshot of a
   running container off the box (its image config embeds runtime secrets).
7. Paid/live lanes (`RUN_REALSTACK=1`, `-k realstack`) require explicit
   per-run owner approval. Free lanes and the §6 checklist do not.
8. Do not prune images/tags/volumes without per-item owner approval:
   rollback tags are backups. The `clean_up` skill is how to ask: it names each item and
   runs only on the owner's go.

After any deploy: run the DEPLOYMENT.md §6 checklist end-to-end and report
the results honestly, including anything skipped.

## Launch a box to a commit

The launch flow takes ONE box to exactly a commit already on `origin/dev`, rebuilds what the
brief names, runs CI, asks the brief's Nessie questions, grades the replies, reviews the commits
it shipped, and reports. It never merges, rebases, commits or pushes. Use it when handed a launch
brief (an instance, an expected sha, the images to rebuild, CI, Nessie questions) or told to
rebuild or launch at an instance.

**One-time setup:** create the local box config `~/.config/nextseek/boxes.json` (or set
`NEXTSEEK_BOXES`) from [`boxes.example.json`](boxes.example.json); fields in
[`references/boxes.md`](references/boxes.md). It is outside the repo and never committed. A missing
file or key stops the scripts with a message naming it.

You fill small JSON forms; `.claude/skills/deploy/scripts/launch.py` validates them and writes the preflight script,
the runner, the watcher, the commit review, the facts and the report. Every rule (known reds,
stop conditions, the nightly time windows, image needs, budgets, the one-ssh rule) lives in
`.claude/skills/deploy/scripts/rules.py`. Never hand-write or hand-edit a file the script writes: fix the form and rerun.

`K=.claude/skills/deploy/scripts`, `S=<your scratchpad>`, run every step as `uv run $K/launch.py ...`.
The launch folder `D` is printed by step 1 (`<reports_dir>/<instance>-launch-<TAG>/`).

**Read every launch output with the Read tool, never `cat` through Bash** (an extra Bash command is
one more approval, and auto mode has refused a plain `cat` of `$D/preflight.out`).

### The short form: the way to run a prod launch (allowed on dev)

Three commands run the steps of the table below in order, in one process, and stop at the first step
that does not exit 0, with that step's exit code. Every rule, refusal and exit code is the step's own,
so one operator approval (Ask mode) covers a whole step. Each prints what you need next.

| Command | Runs | Prints | Then you |
|---|---|---|---|
| `prepare --form $S/brief-form.json` | steps 1 to 3 up to the form: brief, preflight script (`$D/preflight.sh`), ssh preflight, preflight, `git fetch`, commits | the brief lines, the preflight table and verdict, one `COMMIT` line per commit, an `ANNOUNCE` line | say the announcement, fill `$D/commit-review-form.json` |
| `start --brief $D/brief.json --form $D/commit-review-form.json` | steps 3 to 5: review, runner, ssh start | `DELIVER`, the full commit review when there is a parent, the runner summary, the first status lines | SendMessage the review to the parent at once (here it goes as the rebuild begins, not before it) |
| `finish --brief $D/brief.json` | steps 6 to 8: ssh watch, ssh pull, judge. Long: run it with Bash `run_in_background: true`. The watch exits 1 when the runner has not written `ALL_DONE` (nothing pulled: report it stuck) | the judge verdict, `FACTS`, `REPORT_FORM`, one `NESSIE_RUN` line per run | read facts and replies, then step 9, `report`, as usual |

Which mode runs the prod steps (an allow rule, or `!`), the seed-dump handling and the backup: `references/prod.md`.
A rerun after a stop needs `--force` (exit 3: the brief or the review exists). `--after-failure
"<the operator's words>"` passes through to the ssh step, as below.

### Step by step

| # | Step | Command | You write |
|---|---|---|---|
| 1 | Brief | `brief --form $S/brief-form.json` | the brief form (`references/brief-form.md`) |
| 2 | Preflight | `preflight-script --brief $D/brief.json > $S/pre.sh`, then `ssh --brief $D/brief.json --purpose preflight --script $S/pre.sh --out $D/preflight.out`, then `preflight --brief $D/brief.json` | nothing |
| 3 | Commit review | `git -C <workstation_repo> fetch -q origin`, `commits --brief $D/brief.json`, fill `$D/commit-review-form.json`, `review --brief $D/brief.json --form $D/commit-review-form.json` | the review (`references/commit-review.md`) |
| 4 | Deliver the review | `review` prints `DELIVER:`. A named parent: SendMessage to it NOW, before building, with the full text of `$D/commit-review.md`. Parent null: it stays in your report | the message |
| 5 | Runner | `runner --brief $D/brief.json`; announce one line (what, why, cost or "free", minutes); `ssh --brief $D/brief.json --purpose start` | the announcement |
| 6 | Watch | `ssh --brief $D/brief.json --purpose watch --script $D/runner/watch.sh --out $D/watch.out`, with Bash `run_in_background: true`; wait for the notice | nothing |
| 7 | Pull | `ssh --brief $D/brief.json --purpose pull` | nothing |
| 8 | Judge | `judge --brief $D/brief.json`, then read `facts.json`, the logs and every Nessie reply (`references/nessie-questions.md`) | nothing yet |
| 9 | Report | fill the null slots of `$D/report-form.json` (`references/report-form.md`), `report --brief $D/brief.json` | the judgement only |

Your final message is the text of `$D/LAUNCH-REPORT.md`. Examples of each form are in `examples/`.

### Exit codes (every subcommand)

| Code | Means | Do |
|---|---|---|
| 0 | ok | next step |
| 2 | the form, the call or the box config is wrong | read the message, fix the FORM or the config, rerun |
| 3 | the output exists | pick up where you were; `--force` only to redo your own step |
| 5 | a stop rule fired | stop and report the printed problems to the supervisor (`report --no-facts` from `examples/report-stopped.json`); never work around one |
| 6 | outside the time window | report the next allowed start; do not start |
| 7 | the one-connection rule refused the ssh | report; `--after-failure "<operator's words>"` only when the operator clears a retry |

### Per instance

| instance | Read |
|---|---|
| `dev` | `references/dev.md`, `references/runner.md` |
| `prod` | `references/prod.md`, `references/runner.md` |
| `local` | `references/local.md`: no rebuild, no CI, no stack; the brief script refuses one without a quoted ruling |

For every instance: `references/ci.md` (reading CI), `references/nessie-questions.md` (cases, re-pin, grading).

### Launch guard rails

- **Run exactly the brief's steps.** Anything else is a `proposals` row in the report, and runs
  only if `allowed_extras` names it.
- **Only `launch.py ssh` touches a box.** It builds the transport, holds one lock per launch, and
  shuts the door after a failed connection.
- **No secrets** anywhere you write or print: name `CI_WRITE_PASS`, `NEO4J_AUTH`,
  `MYSQL_ROOT_PASSWORD`, never their values.
- **Never, on any box:** `./startup.sh reset`, `docker compose pull`, `docker compose up` without
  `--no-deps`, `--volumes` or a volume prune, `git stash/commit/revert/reset`, edits to `.env`,
  `docker/nextseek.env` or `dmac/local_settings.py`, a full graph sync, a DB write.
- **Red flags:** a refused script step is a report, not a manual retry (exit 5, 6, 7); never
  tweak a rendered runner (re-render from a corrected brief); a sha that moved by a commit is a
  stop, the supervisor decides what ships; grade every Nessie reply, the report refuses a
  question without a verdict and evidence; never start the local stack (it runs out of memory).

Run the launch tests with
`uv run --no-project --with pytest --with pydantic python -m pytest .claude/skills/deploy/tests -q -p no:cacheprovider`
(they read `boxes.example.json`, never your real config).
