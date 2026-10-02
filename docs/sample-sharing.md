# Sharing samples into another project's study

How a superuser links samples of one project into an existing study of another project through the
sample-shares endpoint, reads its dry run, applies it, checks the graph, and undoes it. The skill that drives these
calls step by step is `.claude/skills/share-samples/SKILL.md`; the code is the studies tool's share mode
(`nextseek_api/studies/README.md`, section "Share mode").

## 1. What a share is

A share takes samples of a source project P and links them into an existing study D of a destination project Q:

- for each source assay a sample is in (an assay of a study in an investigation linked to P), the share finds the
  assay of D with the same title and the same internal assays and adds the sample to it, or creates that assay
  in D first (one per title and internal-assay set) and copies the source assay's internal-assay mapping to it;
- each sample keeps the direction it has in its source assay;
- each direct parent of a sample that is a member of the same source assay comes too, as an input (direction 1);
  a grandparent does not, and neither does a parent outside the source project (the dry run lists it with its
  projects, so it can be shared from its own project);
- every shared sample and every parent brought gets project Q.

It only adds. It never removes a sample from an assay or a project, never creates a study, never writes a
publication attribute and never changes a SEEK sharing policy. Moving samples out of an investigation's
Unpublished study into a new paper study is not a share: that is `manage.py studies` (the studies tool).

## 2. Who may run it

A Django superuser whose login is a SEEK person. SEEK authorises each assay the share creates as that person: the
apply call sends the caller's own SEEK login (Basic, or the login the session holds), and the endpoint proves it
is the SEEK person bound to the signed-in user before anything is written. The credential is used inside that
call only and never stored.

## 3. The three calls

The base URL is `$NEXTSEEK_URL`. The credential is a curl config file made once outside every repository (the
skill shows how), passed as `--config`; the password is never on a command line.

**Create (the dry run).**

```bash
curl -sS --config "$C" -H 'Content-Type: application/json' --data @request.json \
  "$NEXTSEEK_URL/nextseek_api/sample-shares/"
```

with `request.json` holding:

```json
{"sample_uids": ["TIS-230324BOO-39-PUB", "TIS-230324BOO-40-PUB"], "source_project_id": 1,
 "destination_project_id": 2558, "destination_study_id": 746}
```

The answer is `202` with `share_id`, `state` (`planning`) and `status_url`. The share worker plans it within
seconds.

**Read the plan.** `GET $NEXTSEEK_URL/nextseek_api/sample-shares/<share_id>/` until `state` is `planned`,
`refused` or `plan_failed`. A planned share's `summary` holds:

- `outcomes`: how many UIDs or samples ended in each outcome: `shared`, `no_change` (already done),
  `sample_uid_not_found`, `sample_uid_not_unique`, `not_in_source_project`, `no_source_assay`,
  `source_assay_unmapped`, `target_assay_ambiguous`;
- `uids`: up to 50 UIDs of each outcome (the run directory's `unmatched.csv` lists every one);
- `groups`: each group's source assay ids, title and internal assays, and whether the destination assay is reused
  (its id) or will be created;
- `links`: the links to insert, by role (`mover`, `parent`); `project_rows`: the project rows to add;
- `parents` and `parents_count`: each parent brought, with the child and the source assay that brought it (the
  first 50; the run directory's `parents.csv` lists every one);
- `parents_outside_source_project` and its count: each parent skipped because it is not in the source project,
  with its child, the source assay and its projects;
- `clone_policy`: the policy each assay the share creates will take (D's, read from SEEK's tables);
- `label_changes_needing_approval`: lineage label changes the graph would only write with the operator's
  approval (normally none; section 6);
- `plan_sha256` and `run_dir`, the run directory's name; on the box it is `<LOG_DIR>/studies/<run_dir>`.

**Apply.** `POST $NEXTSEEK_URL/nextseek_api/sample-shares/<share_id>/apply/` with `{"plan_sha256": "<the plan's
sha>"}`, then act on the answer:

| Answer | Meaning | Do |
|---|---|---|
| `200` `applying` | one destination assay was made | call apply again at once |
| `202` with `code` `clone_outcome_unknown` | SEEK's answer to a create was lost | call again after `retry_after_s` |
| `202` `queued` | every destination assay exists; the links are queued | stop calling; poll the share |

Then poll the share until `applied` (its `receipt` gives the links written, the project rows added and the outbox
key), `apply_failed` or `rolled_back`. Last, `GET .../sample-shares/<share_id>/?verify=graph` (section 4).

## 4. What changes where

- **MySQL.** SEEK's `assay_assets` gains the links and `projects_samples` the project rows, in one transaction with
  the graph sync's outbox row; each assay the share created gets its `assays_internal_assays` rows. SEEK's own
  record of a created assay is SEEK's, made by the caller.
- **The graph**, within seconds, when the outbox row drains: each sample and parent gains project Q (its
  `project_ids` and an IN_PROJECT edge) and an IN_STUDY to D. A sample that sits on a graph-only paper study of
  D's own investigation is not linked to D (it keeps its paper link until the paper moves); a paper sample shared
  into another investigation's study is. `?verify=graph` counts `found`, `has_project`, `in_project`, `in_study`,
  `paper` and `paper_in_study`, lists up to 50 ids with no node, and gives the worst state of the share's outbox
  rows, one per 5,000 samples (`dead`, `failed`, `missing`, `pending` or `done`).
- **NExtSEEK's access** reads `projects_samples` and the graph's project ids, so the destination project's members
  see the samples in graph search, the download API and Nessie as soon as the row drains.
- **SEEK's own UI.** No sharing policy is changed. SEEK lists a shared sample under Q and on the destination
  assay's page, but a member of Q who could not see it before sees it as a hidden item and cannot open it in SEEK.
  An assay the share creates takes D's policy, read from SEEK's tables when the share is planned (SEEK's API
  shows a study's policy only to someone who can manage it) and shown in the dry run as `clone_policy`, so it is
  visible as D is. If people need the samples in SEEK's UI
  too, a SEEK admin changes the samples' sharing in SEEK, outside this tool.

## 5. Project changes made directly in SEEK

A project added to a sample in SEEK's own UI or API writes no outbox row, so it reaches the graph, and with it
non-admin access in NExtSEEK, only at the next nightly reconcile. A share does not have this window: its outbox
row is written in the same transaction as its links and drains in seconds.

## 6. Label changes needing approval

A share adds memberships only, so lineage labels normally stay as they are: a sample and a parent it brought
share the source assay and its destination twin, both mapped to the same internal assays, and the smaller assay id
keeps an edge's `assay_id`. When `label_changes_needing_approval` is not empty (a reused destination assay with a
smaller id than its source, or a protocol renamed under its id), the operator writes them after the share with the
studies tool's graph step on the share's run directory:
`manage.py studies --mode graph --run-dir <LOG_DIR>/studies/<run_dir> --approve-label-changes
--i-mean-the-live-graph`.

## 7. Undo

A share is undone from a shell on the box, by the operator only, with the studies tool's rollback on its run
directory: `manage.py studies --mode rollback --run-dir <LOG_DIR>/studies/<run_dir> --seek-login <login>`, first
without `--confirm` to read what it would undo, then with it. It deletes the links and project rows the share wrote
(only those still there; a project row stays when the sample is still linked into a study of that project, and the
undo reports it), the created assays' internal-assay rows and then the created assays in SEEK (never a reused one),
and resyncs the samples. The share then reads `rolled_back` and is never applied again. There is no undo route.

## 8. Limits and error codes

- At most 10,000 UIDs per share; split a longer list into several shares with the same projects and study.
- One destination study per share; it must already exist and sit in an investigation linked to the destination
  project.

| Code | Where | Meaning | Do |
|---|---|---|---|
| `invalid_request` | create, apply (`422`) | the body is not valid | fix the body |
| `same_project` | the share's `error` (`refused`) | source and destination are one project | pick another destination |
| `source_project_unknown`, `destination_project_unknown`, `destination_study_unknown` | `refused` | an id is not SEEK's | check the ids |
| `destination_study_not_in_destination_project` | `refused` | D's investigation is not linked to Q | check the study and project |
| `destination_policy_unreadable` | `refused` | an assay would be created in D, but D's policy cannot be read from SEEK's tables | ask the operator to check D's sharing in SEEK |
| `plan_failed` | the share's `error` | planning raised | report it with the share id |
| `not_found` | read, apply (`404`) | no share has this id | check the id |
| `seek_credential_missing` | apply (`401`) | no SEEK login came with the call | send Basic or use a session |
| `seek_identity_mismatch`, `seek_refused` | apply (`403`) | the SEEK login is not the caller's, or SEEK refused it | stop; fix the login or the SEEK rights |
| `share_not_applicable` | apply (`409`) | the share is not planned, applying or apply_failed, or it failed for good (`plan_stale`, `destination_changed`) | read its state; make a new share if it says so |
| `share_rolled_back` | apply (`409`) | the share's run was undone | make a new share |
| `plan_changed` | apply (`409`) | the sha is not the share's plan, or the plan was made by code since updated on the box | read the share again; for other code, make a new share |
| `nothing_to_apply` | apply (`409`) | every sample reads `no_change` | nothing to do |
| `not_ready` | apply (`409`) | the studies release is not finished on this box | ask the operator |
| `busy` | apply (`409`) | another studies run holds the lock, or another share is making an assay of the same group in D | wait and call again |
| `destination_changed` | apply (`409`) | D moved or is gone, or now holds several assays of one group | decide in SEEK if needed, then make a new share |
| `clone_outcome_ambiguous` | apply (`409`) | several assays match a create whose answer was lost | decide in SEEK, then ask the operator |
| `seek_payload_rejected`, `clone_payload_invalid` | apply (`422`) | SEEK refused, or could not be sent, an assay's payload | report it with the message |
| `seek_error` | apply (`502`) | SEEK answered 5xx or could not be reached, or three creates of one assay got no answer and nothing shows in SEEK | call again later; after three creates, look in SEEK first |
| `graph_unavailable` | read with `verify=graph`, apply (`503`) | the graph, or the share's run directory, cannot be read; nothing was written | call again later |
| `plan_stale` | the share's `error` (`apply_failed`) | those samples' links or project rows changed since the dry run | make a new share; finished work reads `no_change` |
| any other code (`unit_state_unknown`, `worker_error`, ...) | the share's `error` (`apply_failed`, `plan_failed`) | the link unit or the share worker stopped | report it with the share id; do not retry |

## 9. Links

- The skill: `.claude/skills/share-samples/SKILL.md`.
- The package: `nextseek_api/studies/README.md`.
- Who may call the endpoint: `docs/endpoint-authorization-register.md`.
