# The dev box (`instance: dev`)

Box facts as of 2026-09-25:

| | |
|---|---|
| Repo | `<repo>`, branch `dev`, fast-forwarded to origin/dev |
| Memory | 46.8 GiB (not 64). OOMs on 09-11, 09-12, 09-14. Caps live in the repo-root `.env` |
| Disk floor | 20 GB free (`ci_profile: dev`); the rebuild refuses below it |
| Containers | `nextseek`, `nextseek-nextseek_nginx-1`, `nextseek-sidecar`, `dmac-bedrock-proxy`, `neo4j`, `seek`, `seek-workers`, `seek-mysql`, `seek-solr` |
| Ports on the box | app nginx `127.0.0.1:8000`, SEEK `127.0.0.1:3000` (`localhost:80` 404s and proves nothing) |
| Data | TCGA (918,519 of 985,179 samples) plus one project, "Published Data". SEEK holds one institution, so labs are copied in (section 5). Corpus answer keys are PRODUCTION numbers |
| Windows | 04:00Z-12:00Z the hung nightly dump holds LOCK TABLES READ on every dmac table (blocks logins, chat writes, CI); 05:45Z-07:45Z the nightly graph sync (02:00 US Eastern). The brief, the runner and `ssh --purpose start` all refuse a heavy step that would touch either |

## 1. Transport

Only through `launch.py ssh` (references/runner.md "The one-connection rule, as code"). For the
record, what it builds on dev: `ssh -o BatchMode=yes <dev ssh_host> "echo <b64> | base64 -d | sudo -n
-u <run_as> bash -l"`, the whole remote command one argument. Files go in with `scp` to
`/tmp/` (they land as the login user and are made readable before the sudo hop). Never an interactive-shell alias
from a tool call (it forces a TTY and refuses a command); never plain `sudo` or `sudo docker`
(`<run_as>` is in the docker group). Needs the VPN.

## 2. Preflight (read-only, one connection)

```bash
uv run $K/launch.py preflight-script --brief $D/brief.json > $S/pre.sh
uv run $K/launch.py ssh --brief $D/brief.json --purpose preflight --script $S/pre.sh --out $D/preflight.out
uv run $K/launch.py preflight --brief $D/brief.json
```

The script prints one row per check and writes `$D/preflight.json`. The rows, and what stops the
launch (exit 5), are the table below, as code in `launch.py` (`judge_preflight`) with the numbers
from `.claude/skills/deploy/scripts/rules.py`:

| Check | Stop when |
|---|---|
| complete | the script did not reach its last line |
| disk | under 20 GB free |
| memory, swap | under 10 GiB available; swap full |
| oom, restarts | any container OOMKilled, restarting, or restarted 3+ times (waivable, in the operator's words) |
| busy | a graph sync, drift or `manage.py nessie` inside `nextseek` |
| tmux | another session's `launch-`, `nessie`, `rebuild` or `ci` job |
| branch, fetch, expected, origin_match, ff | not `dev`; fetch failed; the sha missing; origin/dev not the sha; HEAD not an ancestor (never waivable) |
| dirty | anything beyond the known-dirty list below |
| dirty_touched | the range touches a dirty file other than the context refresh |
| nextseek_env | old `/app/chat_nextseek` style paths |
| ci_env | one of `CI_SMOKE_USER`, `CI_SMOKE_PASS`, `CI_WRITE_USER`, `CI_WRITE_PASS` missing |
| member_login | a `*-member.json` cases file in the brief and `CI_SMOKE_USER` or `CI_SMOKE_PASS` missing from ci.env (the row exists only then) |
| labs_source | `/tmp/labs_db.json` missing when app is rebuilt (a warning otherwise). The operator re-stages it: the workstation copy is root-only and never committed |
| http | not 200 |

SEEK over 12 GiB is not a stop: the runner restarts SEEK before a Nessie run.

**Known dirty on dev (safe):** `?? logs/`, `?? startup/.ci-write-run.xml`, and the app's daily
context refresh (` M` on the five `*_db.json` files in `NessieAI/chat_nextseek/src/chat_nextseek/context/`
plus `?? .context_db_refresh` there). The runner discards only the refresh, then pulls. Anything
else dirty is someone's in-flight work.

### Which images the range needs

`launch.py commits` derives them from every changed path with `IMAGE_RULES` in `.claude/skills/deploy/scripts/rules.py`
(from DEPLOYMENT.md section 3.2): app for Python, templates, prompts and anything baked; app then
collectstatic for `static/` and the chat bundle; cc-agent for `NessieAI/docker/cc-runtime/**`; app
AND cc-agent for the six canonical context files and `baml_src`; nextseek-sidecar for
`NessieAI/docker/ns-sidecar/**`; bedrock-proxy for `NessieAI/docker/bedrock-proxy/**` (with
`bedrock-proxy rebuild ok`); nothing for `ci/**` and `startup/**` (they run from the host
checkout). `docker/nginx.conf` (a single-file bind mount: the running nginx keeps the old file until
`docker compose up -d --no-deps --force-recreate nextseek_nginx`), `docker-compose.yml`, migrations
and the seed dumps are flags the brief must name. Order: bedrock-proxy, app, cc-agent,
nextseek-sidecar.

## 3. Runner

`launch.py runner` renders it with `IN=/tmp`, SEEK restarts allowed, and the dev-only labs copy
after an app rebuild (section 5). Start, watch and pull: references/runner.md.

## 4. Timings seen on dev

| Step | Took |
|---|---|
| app rebuild (includes the post-rebuild graph drift check on 985k samples) | 10 to 15 min |
| cc-agent rebuild | about 2 min (node:22 base from rebuild 2 on: budget 5) |
| bedrock-proxy rebuild | about 2 to 3 min |
| CI `--no-nessie` | about 6 min of tests; up to 13 min with the labs restart and waits |
| Nessie | about 1 min per case (NS turns 30 to 60 s, Container-CC 60 to 150 s); the 67-case prod suite took 70 min |

## 5. Things a rebuild resets on dev

An app rebuild recreates `nextseek` from the image:
- `labs_db.json` is gone. Dev's SEEK has one institution, so the labs read is REFUSED and the
  file must be copied back from `/tmp/labs_db.json`, then `docker compose restart nextseek`.
  A restart keeps it; a rebuild or recreate loses it. Healthy afterwards:
  `docker logs nextseek 2>&1 | grep -F "[CONFIG][LABS]" | tail -2` shows about 40 labs with
  `'source': 'previous_file'` (the line appears once a chat config is built).
- `/app/runs` does not exist, and `docker cp` will not create it:
  `docker compose exec -T nextseek mkdir -p /app/runs`.
- Anything running inside the container dies.

The runner does all three for you.

## 6. Pull the evidence

`uv run $K/launch.py ssh --brief $D/brief.json --purpose pull` (a tarball of `~/launch-<TAG>` and its
status file, unpacked into the launch folder).

## 7. Dev gotchas

- **`REBUILD app exit=1` is often not a failure.** A red `graph sync health` line also makes it exit 1; it is
  known on dev only when its one problem is the OP14 drift on `catalog.assistant_investigations`, and any other
  problem on it stops (ci.md, "The graph sync health line"). The rebuild exits 1 when graph drift is red (known:
  1 of N, `catalog.assistant_investigations`) or when there is no GHCR credential for the
  rollback push (loud banner, harmless). The deciding line is `✓ app rebuilt and restarted`.
- **The refresh marker.** `.context_db_refresh` inside the container is fine if its mtime is
  AFTER the container's start; `facts.json` carries `checks.refresh_after_start`. False means it
  was baked into the image and labs stay empty for the UTC day: report it.
- **The sidecar op check.** Since approach 1 (piece 2) the op tools take the sidecar only when the app's
  `NEXTSEEK_CC_OPS_ROAD` says `sidecar` (`CHECK ops_road direct|sidecar`; a build without the switch counts as
  `sidecar`). On the sidecar road the checks compare the running `SIDECAR_OPS` with the repo's at HEAD
  (`CHECK sidecar_ops match|differ`): `differ` after a sidecar rebuild is a finding, and without one it means the
  sidecar is stale (CC calls to a missing op fail `VALIDATION: bad request`). On the direct road the line reads
  `CHECK sidecar_ops skipped` and a stale sidecar is not a finding.
- **SEEK Puma workers bloat** (7 to 9 GB each, container 20 GiB and more). The runner restarts
  `seek` before a Nessie run when it is over 12 GiB.
- **Dev numbers are not prod numbers.** Any numeric red on dev is arithmetic until the number is
  re-measured on dev's own graph.
- **Several Claude sessions share this box.** Check `tmux ls` and the busy line; never kill a
  tmux session you did not start.
