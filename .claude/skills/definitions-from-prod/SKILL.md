---
name: definitions-from-prod
description: >-
  Use when the operator asks to sync, copy or refresh sample types, assays, attributes or the definitions from
  production to dev (or to a local stack): "dev is sample types behind", "copy prod's definitions to dev", "run
  definitions from prod". Reads production read-only, dry-runs `manage.py definitions_from_prod` on the target,
  creates the missing SEEK sample types through the API as the box's CI write account, applies on the operator's
  go, and proves the copy with a second dry run. Never writes production. Not for editing definitions (that is the
  Definitions admin page).
---

# Copy definitions from production to dev

The command and what it copies, recomputes and leaves: `nextseek_api/definitions_from_prod/README.md`. Box hosts and
transports come from `~/.config/nextseek/boxes.json` (`deploy/references/boxes.md`), never from a file in a repo.
Every file this run makes goes under `~/.cache/nextseek-defsync/<UTC time>/` on the laptop, never in a repository.
Below, `<dev ssh>` is the target's transport: a `sudo` box runs
`ssh -o ConnectTimeout=30 -o BatchMode=yes <ssh_host> "sudo -n -u <run_as> bash -lc '<command>'"` (quote the whole
remote command); a `direct` box runs `ssh ... <ssh_host> '<command>'`.

## Gates

| Step | Needs |
|---|---|
| reading production | the operator's own words in this session; one ssh, read only |
| any write on the target | a box slot (the coordinator or the operator) and the operator's go after the dry run |
| the command on the target | the target's image holds it (it is on `origin/dev` and the box was rebuilt since), or its files were copied into the running `nextseek` container (below; no restart; gone at the next rebuild) |
| creating SEEK types | the CI write account is an active member of the target project (below) |

One ssh to a box at a time, `ConnectTimeout=30`, no retry loops or pollers. Before each write step: `ps` on the
laptop for other sessions' ssh to the box and `pgrep -c 'sshd:'` on the box, the box lock
(`flock -n ~/launch.lock true`), and memory. Memory guard: `free -h` available above 6 GiB; the `nextseek` container
under 90% of its cap in `docker stats --no-stream`; `seek-mysql` by `docker inspect -f '{{.RestartCount}}
{{.State.OOMKilled}}' seek-mysql` (no rise, false) and an absolute ceiling just under its cap. `seek-mysql`'s stats
figure includes file cache the kernel reclaims first, so it idles near its cap: a percentage rule stops every run.

**Copying the command in** (when the image lacks it). The files are `nextseek_api/definitions_from_prod/`
(`__init__.py`, `dump.py`, `sync.py`, `apply.py`) and `nextseek_api/management/commands/definitions_from_prod.py`;
the package folder is new, so the tar creates it as root:

```bash
F="nextseek_api/definitions_from_prod/__init__.py nextseek_api/definitions_from_prod/dump.py \
   nextseek_api/definitions_from_prod/sync.py nextseek_api/definitions_from_prod/apply.py \
   nextseek_api/management/commands/definitions_from_prod.py"
tar --owner=0 --group=0 --numeric-owner -cf - $F | <dev ssh: 'docker cp - nextseek:/app &&
  docker exec nextseek sh -c "cd /app && sha1sum <the five paths>"'>
sha1sum $F    # must match the box's lines
```

## The login

On dev and local the login is the box's own CI write account: `CI_WRITE_USER` / `CI_WRITE_PASS` in the box's
`~/.config/nextseek/ci.env` (a Django superuser bound to one SEEK person; the laptop's `ci.env` may name another
account). Read it on the box, inside the remote command; never print it, never put the password on a command
line or in a file of a repository.

## 1. Read production

A dump from earlier the same session is fine when nothing was edited on production since: copy its `prod.json`
into the new `$D`. Otherwise:

```bash
D=~/.cache/nextseek-defsync/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$D"
<prod ssh: 'timeout -k 10 300 docker exec -i nextseek /app/.venv/bin/python -'> \
  < nextseek_api/definitions_from_prod/dump.py > "$D/prod.json"
```

One READ ONLY transaction inside the app container, which holds the credentials; nothing secret leaves the box.

## 2. The input: production's bundle, the hold list, the project

Cells where the target holds newer work than production keep the target's value. The operator rules them item by
item (find candidates in git history of `context/*.json` and SEEK timestamps, never by a rule alone); the list is
`[{"table", "key", "column", "why"}]`, `column` `*` for the whole row, kept beside the run.

```bash
python3 -c 'import json,sys; d=sys.argv[1]; json.dump({"prod": json.load(open(d+"/prod.json")),
  "hold": json.load(open(sys.argv[2])), "project": sys.argv[3]}, open(d+"/input.json","w"))' \
  "$D" <hold list path> "<project the new types join on the target>"
```

## 3. Dry run on the target

```bash
<dev ssh: 'W=$(grep "^CI_WRITE_USER=" ~/.config/nextseek/ci.env | head -1 | cut -d= -f2- | tr -d "\"\x27");
  timeout -k 10 900 docker exec -i -w /app nextseek /app/.venv/bin/python manage.py definitions_from_prod
  --source - --seek-login "$W" --json'> < "$D/input.json" > "$D/dry.json"
```

Read `dry.json` and show the operator, in plain words:
- `plan.counts`: what would be written.
- `preview`: the attribute API's own check of each request it would send: `text` (descriptions, positions), `rules`
  (required, value type, unit, vocabulary, linked type; only when there are such changes), `create`. `errors`
  empty; `text` and `rules` with `counts.unchanged` 0 (anything else re-plans forever); `create` with
  `counts.created` equal to `plan.counts.attribute_creates`; `affected_samples` is how many sample records the
  creates rewrite.
- `plan.blocked`, `plan.held` (every hold entry appears; `expected.unused_holds` lists the ones never read),
  `plan.waiting`, and `plan.expected` (rows only the target has, derived context columns, SEEK's own columns,
  vocabularies, case-only names, `api_refuses`, `seek_type_text`, `tier_titles_missing`).

Stop for the operator's go.

## 4. SEEK sample types the target lacks

`seek_type_payloads` holds one body per type (title, text, title attribute, project). They are created through
NExtSEEK's endpoint as the CI write account, from the box itself, never by this command or by SQL.

SEEK only lets an account put a type in a project it is an active member of. Before posting, check the account's
memberships on the target (read only: `group_memberships` joined to `work_groups`, by `users.login`) and pick a
project it belongs to; on dev every sample type is in "Published Data". A 422 "Can only associate projects that you
are an active member of" means this check was skipped.

```bash
python3 .claude/skills/definitions-from-prod/scripts/post_types.py "$D/dry.json" "<boxes.json repo of the target>" \
  > "$D/post_types.sh"
bash -n "$D/post_types.sh"
<dev ssh: 'bash -s'> < "$D/post_types.sh"
```

The script reads the account from the box's `ci.env`, posts to `http://127.0.0.1:<nextseek port>` one type at a
time, prints status, title and new id, and stops at the first non-2xx with SEEK's answer. In any script fed
through `bash -s`, give each `docker exec -i` a `</dev/null`, or it reads the rest of the script as its input. A 5xx or timeout may still have created the
type: look the title up (`GET /nextseek_api/sample_types/`) before posting it again. Then dry-run again (step 3): the
new types' attributes, clades and context rows are now in the plan, and their payloads are gone.

## 5. Apply

Step 3's command with `--apply` (it returns as soon as the attribute job is accepted; `--wait S` waits). Creates
past the attribute API's threshold run as one job in the app's worker. Then dry-run again later (no polling loop).
While actions remain, apply again: every run is idempotent, and a run that finds an attribute job still going
writes no attributes, names the job and exits 1. A context row whose fields that job creates lands on the run after
(until then its tiers step reports an error and the run exits 1: expected). Unfinished jobs whose lease expired long
ago (a crashed run the recovery loop never closed) are listed under `stale_jobs` and do not hold the sync back;
leave them, and tell the operator.
Watch the box while the job runs: `free -h`, and the `nextseek` and `seek-mysql` containers in `docker stats`; if
memory climbs toward a container's cap, stop and tell the operator.

## 6. Prove it

- The last dry run's `plan.counts` are all zero; `plan.blocked` and `plan.expected` list only what step 3 showed.
- The graph caught up: `manage.py graph_sync_health` (the outbox drained) and
  `manage.py graph_sync --context --dry-run --json` (read-only: the derived columns the context step would set).
- Hand the operator the counts and the expected list; the next session needs the target-only rows.
