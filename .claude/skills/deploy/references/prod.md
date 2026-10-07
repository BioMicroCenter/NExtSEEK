# Production (`instance: prod`)

Production is a live shared system with real researchers on it. On prod this skill deploys and
runs CI. It asks Nessie questions ONLY when the brief says `prod_nessie: yes` (each question
creates a chat and task rows as the CI superuser, or as the non-admin smoke login for a
`*-member.json` cases file; that is the one write accepted here), and never
runs exploratory commands, one-off scripts in a container, or anything else that writes. The operator's
older standing rule was "no ssh to prod at all"; the brief naming `prod` is the operator's
explicit ask that lifts it for exactly the brief's steps.

**Run a prod launch with the short form** (SKILL.md, operator ruling 2026-10-05, option B): `prepare`,
then `start`, then `finish` (in the background), then `report`. In Ask mode each is one approval for a
whole step; the step-by-step commands would ask about a dozen times. Read every output with the Read
tool, never `cat` through Bash: auto mode refused even a local read of `$D/preflight.out`.

### Which mode runs it (operator ruling 2026-10-07: an allow rule)

Auto mode's classifier refused every prod step on 10-03, 10-04 and 10-05 (`brief` as "Production Deploy",
the preflight and even a local `cat` of its output as "Production Reads"), and a spawned session is in auto
mode. The operator's choice is an allow rule for exactly the four launch commands plus a Read of the launch
folders. **Not verified**: whether the classifier honours it was not testable from the session that wrote
this (it refused a docs lookup about itself), and a session may not add the rule itself (refused on 10-03
as Self-Modification). The operator adds it to `~/.claude/settings.json` (or `.claude/settings.local.json`):

```json
{"permissions": {"allow": [
  "Bash(uv run .claude/skills/deploy/scripts/launch.py prepare *)",
  "Bash(uv run .claude/skills/deploy/scripts/launch.py start *)",
  "Bash(uv run .claude/skills/deploy/scripts/launch.py finish *)",
  "Bash(uv run .claude/skills/deploy/scripts/launch.py report *)",
  "Read(//home/cdemurjian/code/dmac/docker/CI-reports/**)"
]}}
```

The rule matches the command string, so run exactly that form (`uv run .claude/skills/deploy/scripts/launch.py
prepare --form ...`, relative path, from the repo root; not `python`, not an absolute path). Leave the `start`
line out to keep `start` (the one command that begins a rebuild) as the single thing the operator approves or
types. **If a step is still refused: do not retry or rephrase.** The operator types the same command with a
`!` in front (`! uv run .claude/skills/deploy/scripts/launch.py start ...`): it skips the classifier and its
output lands in the chat, so nothing needs reading from a file. Ask mode (one approval per step) also works.

### The range of 2026-10-07 (aa85faf9 to the round 6 tip), in order

1. **Seed dumps.** The range changes `startup/seed/` data (seed refresh 46cba3ac), and prod holds LOCAL edits to
   `neo4j.cypher.gz` and `seek_production.sql.gz` (its live dumps: passwords, sessions, emails). The runner now
   copies those two to `~/backups/seed-dirty-<tag>/` (dir 0700, files 0600, byte-compared), resets them in git,
   then pulls (status line `SET_ASIDE`). It does so only for a file the range changes. The brief acknowledges
   the seed DATA paths `commits` lists (`dmac.sql.gz`, both dumps, `sql/assay_context.sql`) with the operator's
   words in `acknowledged_flags`; seed tooling and docs no longer flag. Not covered: a prod edit to any OTHER
   tracked file the range changes still stops at preflight (`dirty_touched`).
2. **Migrations** (0025, 0026, and round 6's 0027): the brief needs `migrations_expected: true` and
   `allowed_extras: ["db backup ok"]`. The skill takes no backup. The operator takes it by his runbook
   (`mysqldump -h 127.0.0.1`, stored outside the checkout, 0600) BEFORE `start`; the extra only records that it is done, so write the dump's path in
   the report. 0025 creates `assistant_cc_turn` and adds one column to an existing parent table; 0026
   only adds columns to the new table.
3. **Compose**: `docker-compose.yml` changed (laya-router behind profile `laya`, `${INSTANCE_PREFIX:-}` names),
   so `compose_change_expected: true`. Container names are unchanged on prod (the prefix is empty). The
   preflight `compose_config` row checks the file ALREADY on the box (the old one), not the incoming one; a
   local `docker compose config -q` of the new file passes without `docker/laya.env`.
4. **Images**: the range needs `app`, `cc-agent` and `nextseek-sidecar` (the sidecar was last rebuilt on prod
   on 09-22). `commits` lists the newest 50 of ~220 commits; older ones go in `covers_skipped`.
5. **Order and timing.** Backup (2), then `prepare` (its preflight must be under 90 min old at `start`), then
   `start`. Prod has no stop window: do not start at the time of the nightly `mariadb-dump`.

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

`prepare` runs it (or the same three commands as dev.md section 2). The prod script adds four rows, and the floors differ:

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
dump: passwords, sessions, emails; the runner sets them aside when a range changes them, see above), `?? attributes_error.txt`, `?? dmac/local_settings.py.bk`,
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
