# Production (`instance: prod`)

Production is a live shared system with real researchers on it. On prod this skill deploys and
runs CI. It asks Nessie questions ONLY when the brief says `prod_nessie: yes` (each question
creates a chat and task rows as the CI superuser; that is the one write accepted here), and never
runs exploratory commands, one-off scripts in a container, or anything else that writes. The operator's
older standing rule was "no ssh to prod at all"; the brief naming `prod` is the operator's
explicit ask that lifts it for exactly the brief's steps.

The full hand runbook, with a "found" line for every trap, is
the operator's local prod-commands runbook (not in git). Its steps 2, 3, 5 and 8
(backups, `.env` caps, context write, graph sync) are NOT part of a routine launch: run one only
if the brief names it.

Box facts as of 2026-09-24:

| | |
|---|---|
| Access | `ssh -o BatchMode=yes <ssh_host>`: key login straight in as `<run_as>`, no sudo. Needs the VPN (a port 22 timeout is the VPN) |
| Repo | `<repo>`, branch `dev`, fast-forwarded to origin/dev |
| Memory, disk | 62 GiB; disk floor 30 GB (`ci_profile: prod`; 125 GB free on 09-23) |
| Data | the real projects, 168,482 samples, 54 SEEK institutions (labs load natively), NO TCGA |
| Clocks | the app side is UTC, the host shell is US Eastern: `docker logs --since 2026-09-23T18:34:00Z` |
| Last launches | all 2026-09-23: app + cc-agent on c9336c82, again on b2352f55, then app only on b3d064f6. The sidecar was last rebuilt on 2026-09-22 |

## 1. Transport

Only through `launch.py ssh`. On prod it builds `ssh -o BatchMode=yes <ssh_host> "echo <b64> |
base64 -d | bash -l"` (key login straight in as `<run_as>`, no sudo layer) and copies files
with `scp -o BatchMode=yes` to `<home>/`. Never `docker compose pull`: prod's neo4j
image is the unpinned `neo4j`.

## 2. Preflight (read-only, one connection)

The same three commands as dev.md section 2. The prod script adds four rows, and the floors differ:

| Check | Stop when |
|---|---|
| disk | under 30 GB free |
| seed_touched | the range touches `startup/seed/neo4j.cypher.gz` or `seek_production.sql.gz` (the dirty production dumps would collide) |
| compose_files | `NessieAI/docker/bedrock-proxy/proxy-secret.env` or `docker/seek-nginx.conf` missing |
| compose_config | `docker compose config` refuses |
| posterior_routing | `NEXTSEEK_POSTERIOR_ROUTING_ENABLED=1` (a misconfigured box: report) |
| busy, tmux | anything running: a rebuild would kill it |

There is no labs row on prod: labs load natively from SEEK's 54 institutions.

**Known dirty on prod (leave alone, never commit, never print their contents):**
` M startup/seed/neo4j.cypher.gz`, ` M startup/seed/seek_production.sql.gz` (a real production
dump: passwords, sessions, emails), `?? attributes_error.txt`, `?? dmac/local_settings.py.bk`,
`?? test.txt`, `?? logs/`, plus the context-refresh files the runner discards.

## 3. Runner

`launch.py runner` renders it with `IN=<home>`, no labs copy (ever), and SEEK
restarts only with `seek restart ok` (about a minute of errors for live users).

A range with a migration needs a database backup first (the runbook's backup step; `mysqldump` needs
`-h 127.0.0.1` there, or it leaves a 20-byte file). The brief refuses a prod migration without
`db backup ok`; the backup itself is not in the runner: run it only as the brief says, before the
start, through `launch.py ssh --purpose read`.

Start, watch, pull: references/runner.md.

## 4. After the rebuild, on prod

- `labs_db.json` must exist and `docker logs nextseek 2>&1 | grep -F "[CONFIG][LABS]"` must show
  labs fetched from SEEK. A REFUSED labs read on prod is a real defect: report it. NEVER copy the
  workstation's or dev's labs file here.
- Timings seen: app rebuild about 8 min, cc-agent about 2 min, CI 4 to 5 min.

## 5. Nessie on prod (only with `prod_nessie: yes`)

- Never a family that writes or launches: `entity_write`, `pipeline_launch`,
  `pipeline_output_reingest`, `batch_upload_preparation`, or any `write.*` case. The brief script
  refuses a cases file that holds one (exit 5); still read every question, since a question can
  write from any family.
- Numbers in probe files are workstation or dev measurements: re-pin (nessie-questions.md)
  against prod's graph, read-only, before the run.
- Keep it small and sequential: one runner, one cases file at a time, SEEK memory checked first.

## 6. Prod gotchas

- CI's first run right after a restart once hit 4 Playwright `page.goto /login/` timeouts; the
  re-run was green (279 passed). The runner's `--wait-ready` exists for this. If every failure is
  a login timeout, propose a CI-only re-run: a new brief with `images: []`, `ci: true` (its own tag).
- SEEK Puma workers bloat (21.9 GiB after 19 h on 09-23). Report the number; restart only if
  allowed.
- Rolling back is the operator's decision: report the rollback tag from the rebuild log
  (`pre-<utc>-<sha>`) and stop.
