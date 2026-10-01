---
name: share-samples
description: >-
  Use when an admin asks to share, link or copy NExtSEEK samples of one project into an existing study of another
  project ("add these samples to that study too", "share these UIDs with project Y", "link these samples across
  projects"), given sample UIDs, a source project, a destination project and a destination study. Drives the
  superuser-only sample-shares endpoint: dry run, show the plan, apply only on approval, poll, verify in the graph.
  Not for moving samples out of a bucket into a new paper study (that is `manage.py studies`).
---

# Share samples into another project's study

A share copies sample-assay links of the source project into an existing study of the destination project. It
reuses a destination assay with the same title and internal-assay mapping, or creates one (as the caller, in SEEK),
adds the destination project to each sample, and brings each shared sample's direct parents that sit in the same
source assay, as inputs. It never removes a link, never creates a study and never changes a SEEK sharing policy. Read
`docs/sample-sharing.md` for what changes where; this skill is the procedure.

## Before anything

1. **Which box.** Ask the operator: local, dev or production. Local first for anything new. On production, only a
   share the operator asked for, and only after the dry run below is read in full.
2. **The base URL.** The operator gives it. Never write a host into a file of a repository. Shell state does not
   carry from one command to the next here, so every command below sets `NEXTSEEK_URL=<base URL>` and
   `C="$HOME/.config/nextseek/share-<box>.curlrc"` at its start.
3. **The credential.** The operator's own login: a Django superuser bound to a SEEK person. The password must never
   pass through the chat, a command line, a file in a repository or a log. Ask the operator to create a curl config
   outside every repository, once per box, in their own terminal:

   ```bash
   umask 077; mkdir -p "$HOME/.config/nextseek"
   read -r LOGIN; read -rs PW
   esc() { local s=${1//\\/\\\\}; printf '%s' "${s//\"/\\\"}"; }   # curl reads \ and " in quotes as escapes
   printf 'user = "%s:%s"\n' "$(esc "$LOGIN")" "$(esc "$PW")" > "$HOME/.config/nextseek/share-<box>.curlrc"
   unset PW
   ```

   Use it only as `curl --config "$HOME/.config/nextseek/share-<box>.curlrc" ...`. Never read, print, copy or search
   that file. When the share is done, remind the operator to delete it.
4. **The inputs.** A JSON file holding a list of sample UIDs (strings, as NExtSEEK shows them: no `-PUB` stripping),
   the source project id, the destination project id and the destination study id. Check the file parses, count the
   UIDs, list duplicates (the endpoint refuses them). More than 10,000 UIDs: split into several shares with the same
   projects and study, and run them one after another. Confirm the ids by name with the operator:
   `GET $NEXTSEEK_URL/nextseek_api/projects/<id>/` and `GET $NEXTSEEK_URL/nextseek_api/studies/<id>/`.
5. **Where results go.** Save every answer under `$HOME/.cache/nextseek-shares/<box>/<share_id>/`, never inside a
   repository: the answers hold sample UIDs.

## 1. Dry run

```bash
NEXTSEEK_URL=<base URL>; C="$HOME/.config/nextseek/share-<box>.curlrc"
jq -n --slurpfile uids uids.json --argjson p <source> --argjson q <destination> --argjson d <study> \
  '{sample_uids: $uids[0], source_project_id: $p, destination_project_id: $q, destination_study_id: $d}' \
  | curl -sS --config "$C" -H 'Content-Type: application/json' --data @- "$NEXTSEEK_URL/nextseek_api/sample-shares/"
```

The answer is 202 with `share_id` and `status_url`. Poll `GET $NEXTSEEK_URL/nextseek_api/sample-shares/<share_id>/`
every 5 s until `state` is `planned`, `refused` or `plan_failed`. After 10 minutes still `planning`, stop and tell the
operator the share worker may not be running on the box (`run_share_jobs`); do not create another share.

`refused` names one reason (for example `same_project`, `destination_study_not_in_destination_project`): report it
and stop.

## 2. Show the plan, then stop

From `summary`, show the operator, in a short table first:

- samples shared, `no_change`, and every other outcome with its count (`sample_uid_not_found`,
  `sample_uid_not_unique`, `not_in_source_project`, `no_source_assay`, `source_assay_unmapped`,
  `target_assay_ambiguous`), with the UIDs of each (all of them when fewer than 50, else the saved file's path);
- the groups: each source assay title and internal assays, and whether the destination assay is reused (its id) or
  will be created, and the policy a created one takes (`clone_policy`);
- links to insert by role (`mover`, `parent`) and the project rows to add;
- every parent brought in, with the child and the assay that brought it (the first 50; every one is in
  `parents.csv` in the run directory);
- every parent skipped as outside the source project, with its projects
  (`parents_outside_source_project`): say they can be shared from their own project;
- label changes needing approval (normally none; if any, say the operator will run the graph step after the share);
- `plan_sha256` and the run directory name (on the box: `<LOG_DIR>/studies/<run_dir>`).

Then ask the operator to approve THIS plan, naming the share id and the counts. Apply only on an explicit yes for this
share. Anything else: stop; the share stays planned and harmless.

## 3. Apply

Call apply with the plan's sha, and act on each answer:

```bash
NEXTSEEK_URL=<base URL>; C="$HOME/.config/nextseek/share-<box>.curlrc"
printf '{"plan_sha256": "%s"}' "<plan_sha256>" \
  | curl -sS --config "$C" -H 'Content-Type: application/json' --data @- \
    "$NEXTSEEK_URL/nextseek_api/sample-shares/<share_id>/apply/"
```

| Answer | Do |
|---|---|
| 200 `applying` | call apply again at once (it made one destination assay; `clones_remaining` says how many are left) |
| 202 with `code` `clone_outcome_unknown` | wait `retry_after_s`, call again (it is checking whether SEEK finished a create) |
| 202 `queued` | stop calling apply; go to step 4 |
| 409 `busy` | another studies run holds the lock, or another share is making an assay of the same group: wait 30 s and call again, at most 10 times, then ask the operator |
| 503 `graph_unavailable` | nothing was written: wait 60 s and call again, at most 5 times, then ask the operator |
| 409 `plan_changed`, `share_not_applicable`, `share_rolled_back`, `nothing_to_apply`, `not_ready`, `destination_changed`, `clone_outcome_ambiguous` | stop and report |
| 401 (`seek_credential_missing`), 403 (`seek_refused`, `seek_identity_mismatch`), 422 (`seek_payload_rejected`, `clone_payload_invalid`), 502 (`seek_error`) | stop, show the message; never retry in a loop. Once the cause is fixed, the next apply call resumes where the journal stopped |

## 4. Wait for the link unit

Poll the share every 5 s until `applied`, `apply_failed` or `rolled_back`. `applied` carries the receipt: links
inserted, project rows added, the outbox key. `apply_failed` with `plan_stale` means someone changed those samples'
links since the dry run: tell the operator, and offer a new dry run with the same inputs (finished work then reads
`no_change`). Any other code in `error` (`unit_state_unknown`, `worker_error`, ...): report it with the share id and
do not apply again.

## 5. Verify in the graph

Poll `GET .../sample-shares/<share_id>/?verify=graph` every 10 s until `graph.outbox` is `done` (at most 10 minutes;
`failed`, `dead` or `missing`: stop and point the operator to
`GET $NEXTSEEK_URL/nextseek_api/admin/graph-sync/status/`). Then check and report:

- `found`, `has_project` and `in_project` equal the block's `ids` (the shared samples plus the parents brought);
- `in_study` equals `found` less the paper samples that are not linked (`paper` minus `paper_in_study`); a paper
  sample is left unlinked only when its paper sits in the destination study's own investigation, by design, so report
  that count and say so;
- `missing_ids` is empty.

Any other number: report it with the ids; do not try to repair it.

## 6. Report

One short summary: box, share id, samples shared, destination assays created and reused, parents added, project rows
added, skipped by reason, the graph check, label changes left for the operator (with the command
`manage.py studies --mode graph --run-dir <LOG_DIR>/studies/<run_dir> --approve-label-changes
--i-mean-the-live-graph`, which only the operator runs), and where the answers are saved.

## Undo

Only the operator, from a shell on the box:
`manage.py studies --mode rollback --run-dir <LOG_DIR>/studies/<run_dir> --seek-login <login>`, first without
`--confirm` to read what it would undo. Never offer it as an automatic step.

## Never

- the password in the chat, an argument, a repository or a log; reading the curl config;
- apply before the operator approved that `plan_sha256`;
- a retry loop on 401, 403, 422 or 502;
- a share on production without its dry run read in full;
- changing SEEK sharing policies to "finish" a share: not part of it (SEEK's own UI access is a separate admin
  decision, `docs/sample-sharing.md`).
