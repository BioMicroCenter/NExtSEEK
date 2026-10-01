# The studies tool

`manage.py studies` creates a SEEK study if it is missing and moves a list of samples into it, in SEEK: from their
investigation's holding study (its one study whose title ends in "Unpublished", the bucket) into assays cloned into
the new study, with DOI and PMID written on the samples. graph_sync carries every change into the graph. Three
sources feed the same operation: a curator sheet, an export of the dev graph, and the graph-only paper studies.

## Modes

| Mode | Does | Writes |
|---|---|---|
| `export` | on the dev box: the dev graph's paper studies (a Study with a DOI or PMID, never a bucket), or `--study-ids` | the file only |
| `plan` | one source (`--sheet`, `--dev-export`, `--graph-only all\|IDS`, `--associations`), then the plan | the run directory only |
| `apply` | the plan into SEEK; resumes an unfinished run | SEEK (studies, cloned assays), the clones' internal-assay rows, sample links, DOI and PMID, the outbox |
| `graph` | the graph step, with `--approve-label-changes` | the graph, through graph_sync |
| `rollback` | undoes a run in reverse; without `--confirm` it only lists | everything apply and graph wrote |
| `report` | the plan's summary and the journal's progress | nothing |

`--investigation ID` keeps one investigation's targets (a migration wave is `--graph-only all --investigation ID`).
`graph` and `rollback` need `--i-mean-the-live-graph` against the live stack's Neo4j. Exit status: 0 done; 1 stopped
part way (the journal says where; the same command resumes); 2 refused, nothing written.

A run: `plan`, read `plan.txt` and `unmatched.csv`, `apply`, `graph`, then `plan` again: a complete run plans nothing
(every sample reads `no_change`).

## The run directory

Under `LOG_DIR` (`logs/` in the checkout, which git ignores), `studies/<UTC time>-<source>` unless `--run-dir`:
`associations.json` (the input), `plan.json`, `plan.txt`, `unmatched.json` and `unmatched.csv` (every row not matched
and every sample skipped, with its reason: the curators' worklist), then `journal.jsonl` and `graph/` (graph_sync's
archives, `in_study_removed.tsv` and `study_nodes_removed.jsonl` among them). It holds a box's titles, sample ids and
UIDs: it stays on the box.

## The SEEK login

No SEEK service credential exists: the tool acts as the operator, and SEEK authorises each study and assay create as
that person. `--seek-login` names the login; the password is typed at the prompt, or, for
`docker compose exec -T`, given as one line of stdin:

```bash
read -rs PW
printf '%s\n' "$PW" | docker compose exec -T nextseek uv run manage.py studies --mode apply --run-dir <run> \
  --seek-login <login> --seek-password-stdin
unset PW
```

Never `echo` it and never put it on a command line or in an environment variable: both show in process listings.
The tool keeps it in memory only, never in the run directory or a log. Before any write, SEEK must answer
`GET /people/current` with one person, bound to a NExtSEEK user who is a superuser. One run at a time per box: apply,
graph and rollback hold the MySQL named lock `nextseek_studies`.

## The sheet contract

One row per (study, sample). Headers are trimmed and lowercased.

| Column | Required | Meaning |
|---|---|---|
| `study_title` | yes | the study the sample goes into; an existing study of the investigation when exactly one carries the title (case and surrounding whitespace aside), else a new one |
| `investigation_title` | yes | a SEEK investigation, exactly one of that title; investigations are never created |
| `sample_uuid` (also read as `sample_uid`) | yes | the sample's UID, literal: a `-PUB` suffix is never stripped from a sheet |
| `study_description` | no | a new study's description; for an existing study a different one is reported, never written |
| `doi`, `pmid` | no | written on every sample the run adds to the study, every DOI of a sample in several papers kept |
| `seek_study_id` | no | names an existing study; its title must then equal `study_title` |

Formats: `.xlsx` (the first sheet, or `--sheet-name`), `.csv`, `.json` (a list of objects). An entirely blank row is
skipped and row numbers stay the sheet's. An exact duplicate row (same study, same sample) is dropped and counted.

These stop the plan and write nothing: a blank required cell; a study claimed under two investigations; a sample
claimed under two investigations (it may sit in several studies of one); two different descriptions, DOIs, PMIDs or
SEEK study ids for one study; a study none of whose UIDs matches a sample. A UID that matches nothing, or matches two
samples, is not a refusal: it is listed in `unmatched.csv`. A study titled like a bucket, a title another
investigation holds, or a title two studies of the investigation hold refuses that study only.

## What the plan decides

- A sample's source assays are its assays in the bucket; a sample in none of them but in another study of the
  investigation (published once already) is copied from there and never removed. Skipped whole, and listed: a sample
  in no assay; one with any assay in another investigation's study; one whose source assay has no internal-assay
  mapping; one sharing no project with the investigation, or with a parent that shares none.
- Each touched source assay is cloned into the target study with its exact title, class, type, technology type,
  description, tags, SOPs, organisms, creators and policy; in an existing study, the one assay of that title and
  internal-assay mapping is reused. The clone gets a copy of the source's internal-assay rows.
- Each mover goes into its clone with its direction; each parent of a mover in the same assay goes in with direction 1
  and stays; a mover leaves the bucket's assay only when none of its children there stays.
- A new study takes its bucket's sharing policy.
- The label changes the graph step will write are listed in `plan.txt`: the move's own, and differences already
  pending on those edges. The graph step refuses when the live graph would write anything else.

## Journal, resuming, rollback

`journal.jsonl` is append-only; each line is on disk before the write it announces. A POST whose answer was lost is
looked for in SEEK for a while before a new POST; a unit whose commit was not journaled is decided by its outbox row.
`rollback` undoes one run, or one investigation of it, in reverse, and restores only rows unchanged since, listing the
others; a rolled-back run is closed (plan again for a new run).

## Share mode

A superuser can also link samples of one project into an existing study of another project: the sample-shares
endpoint plans a share (a one-unit plan of mode `share`), creates any destination assay as the caller one call at a
time, and the share worker (`manage.py run_share_jobs`) runs its link unit, which adds the destination project to
each sample and parent in the same transaction. `docs/sample-sharing.md` is the how-to; a share is undone with
`--mode rollback` on its run directory.

A move treats a sample's membership in another investigation's study as a share, not a misfiling, when one of the
sample's projects is linked to that investigation, so the sample moves and the plan warns `shared_elsewhere`. A known
limit, accepted: a project linked to both investigations makes a misfiling read as a share.

## Answers of the local checks

Measured on the local stack before the tool's first write anywhere; the provisional values in `seek.py` and
`planner.py` follow them.

| # | Question | Answer |
|---|---|---|
| 1 | Does a PATCH of an assay's `relationships.samples` replace the whole list? | not yet measured |
| 2 | Can SEEK delete an empty study and an empty assay over REST, and does it refuse one with links? | not yet measured |
| 3 | Do `GET /studies/<id>` and `GET /assays/<id>` return `policy` in a form `POST` takes back? | not yet measured |
| 4 | Does an EXP assay need `technology_type`? | not yet measured |
| 5 | What policy does SEEK give a new study or assay when none is passed? | not yet measured |
| 6 | How long may `studies.title` and `assays.title` be? | not yet measured (the planner assumes 255) |
| 7 | Do links written by SQL show on SEEK's pages and in its search without a reindex? | not yet measured |
| 8 | How long do `POST /studies` and `POST /assays` take, and when does a timed-out object show in MySQL? | not yet measured (`WRITE_TIMEOUT_S` 120 s, `ADOPT_WAIT_S` 300 s provisional) |
| 9 | Which direction does Rails write when a sample is added to an assay in SEEK's UI? | not yet measured |
| 10 | Does Basic auth to `/people/current` work from inside the app container? | not yet measured |
| 11 | Can the operator's login create a study, and an assay in it, in each investigation a migration touches? | not yet measured |

## Code and tests

| Module | Holds |
|---|---|
| `models.py` | the input model (`AssociationSet`) and the plan model |
| `buckets.py` | the bucket rule, shared with the registration resolver |
| `sources/` | the three adapters and the shared matching; `dev_investigations.json`, the dev-to-SEEK investigation title map |
| `snapshot.py` | every read the plan makes |
| `planner.py` | `plan_study_moves` |
| `seek.py` | the operator's SEEK session |
| `journal.py`, `mapping.py`, `links.py`, `preflight.py`, `apply.py`, `rollback.py`, `report.py` | apply, the graph step, rollback, the run directory |

The graph writes are graph_sync's (`nextseek_api/graph_sync/paper_studies.py`); the writer registry names the tool
WR-33 (`ci/writers.py`). The suite, `nextseek_api/studies/tests/`, uses synthetic data only and blocks in GitHub CI
(`ci/blocking_lanes.py`); it runs in the Django lane of `nextseek_api/README.md` ("Running and testing"). The
Container-CC copy of the bucket rule is tested in the cc-runtime unit lane of `NessieAI/tests/README.md`; the graph
statements on a real Neo4j in `NessieAI/tests/chat_nextseek/graph_scope/test_paper_studies_lane.py`.
