---
name: definitions-sync
description: >-
  Use when the operator asks to sync, copy or refresh sample types, assays, attributes or the definitions from
  production to dev (or to a local stack): "dev is sample types behind", "copy prod's definitions to dev", "re-run
  the definitions sync". Reads production read-only, dry-runs `manage.py definitions_sync` on the target, has the
  operator create the missing SEEK sample types, applies on the operator's go, and proves the copy with a second
  dry run. Never writes production. Not for editing definitions (that is the Definitions admin page).
---

# Sync definitions from production to dev

The command and what it copies, recomputes and leaves: `nextseek_api/definitions_sync/README.md`. Box hosts and
transports come from `~/.config/nextseek/boxes.json` (`deploy/references/boxes.md`), never from a file in a repo.
Every file this run makes goes under `~/.cache/nextseek-defsync/<UTC time>/` on the laptop, never in a repository.

## Gates

| Step | Needs |
|---|---|
| reading production | the operator's own words in this session; one ssh, read only |
| any write on the target | a box slot (the coordinator or the operator) and the operator's go after the dry run |
| creating SEEK sample types | the operator's login, through a curl config they make themselves (below) |

One ssh to a box at a time, `ConnectTimeout=30`, no retry loops or pollers. Check `ps` for other sessions' ssh first.

## 1. Read production

```bash
D=~/.cache/nextseek-defsync/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$D"
ssh -o ConnectTimeout=30 -o BatchMode=yes <prod ssh host> \
  'timeout -k 10 300 docker exec -i nextseek /app/.venv/bin/python -' \
  < nextseek_api/definitions_sync/dump.py > "$D/prod.json"
```

A `sudo` transport box quotes the whole remote command: `"sudo -n -u <run_as> bash -lc '<command>'"`. The script runs
one READ ONLY transaction inside the app container, which holds the credentials; nothing secret leaves the box.

## 2. The hold list

Cells where the target holds newer work than production keep the target's value. The operator rules them item by
item; find candidates in the first dry run's differences and in git history (`context/*.json`) and SEEK timestamps,
never by a rule alone. Keep the list next to the run, as `[{"table", "key", "column", "why"}]` (`column` `*` holds
the whole row). Build the input:

```bash
python3 -c 'import json,sys; d=sys.argv[1]; json.dump({"prod": json.load(open(d+"/prod.json")),
  "hold": json.load(open(sys.argv[2])), "project": sys.argv[3]}, open(d+"/input.json","w"))' \
  "$D" <hold list path> "<project new types join on the target>"
```

## 3. Dry run on the target

```bash
ssh -o ConnectTimeout=30 -o BatchMode=yes <dev ssh host> "sudo -n -u <run_as> bash -lc \
  'timeout -k 10 900 docker exec -i -w /app nextseek /app/.venv/bin/python manage.py definitions_sync \
   --source - --seek-login <login> --json'" < "$D/input.json" > "$D/dry.json"
```

Read `dry.json` and show the operator, in plain words: `plan.counts` (what would be written), `preview` (the
attribute API's own check of its writes: `affected_samples` is how many sample records get rewritten, `errors` must
be empty), `plan.blocked`, `plan.held`, `plan.waiting` and `plan.expected` (every difference left on purpose: rows
only the target has, derived context columns, SEEK's own columns, vocabularies, case-only names). Stop for the go.

## 4. SEEK sample types the target lacks

`seek_type_payloads` holds one body per type. They are created through NExtSEEK's endpoint as the operator, never
by this command or by SQL. The operator makes a curl config once, in their own terminal, as `share-samples` step 3
describes (`~/.config/nextseek/defsync-<box>.curlrc`); never read, print or copy it. Then, one at a time:

```bash
python3 -c 'import json,sys; [json.dump(b, open(f"{sys.argv[1]}/type-{i}.json","w"))
  for i, b in enumerate(json.load(open(sys.argv[1]+"/dry.json"))["seek_type_payloads"])]' "$D"
curl -sS --config ~/.config/nextseek/defsync-<box>.curlrc -H 'Content-Type: application/json' \
  -X POST "<target base URL>/nextseek_api/sample_types/" --data @"$D/type-0.json"
```

A timeout or a 5xx may still have created it: look the title up (`GET .../nextseek_api/sample_types/`) before
sending it again. Then dry-run again (step 3): the new types' attributes, clades and context rows are now in the plan.

## 5. Apply

Step 3's command with `--apply --wait 0`. Creates past the attribute API's threshold run as one job in the app's
worker; `--wait 0` returns at once and the job goes on. Wait, then dry-run again; while actions remain (a job still
running, a context row whose fields that job creates), apply again: every run is idempotent.

## 6. Prove it

- The last dry run's `plan.counts` are all zero apart from `blocked`; `plan.expected` lists only what step 3 showed.
- The graph caught up: `manage.py graph_sync_health` (the outbox drained) and
  `manage.py graph_sync --context --dry-run --json` (read-only: the derived columns the context step would set).
- Hand the operator the counts and the expected list; the next session needs the target-only rows.
