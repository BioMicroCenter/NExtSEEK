# `nextseek_api/graph_sync/`

## What this is

The one writer of the Neo4j sample graph that `graph_search` reads, and the sync that keeps that graph equal to
MySQL. It reads SEEK's `seek` connection and the dmac `default` connection and writes graph schema v1.2: every
sample with its non-empty metadata as properties, a `T_<code>` type label, `OF_TYPE`, `project_ids`, `IN_PROJECT`,
`search_text`, the parent lists and a `source_hash`; DERIVED_FROM lineage with its assay and protocol labels; the
SampleType and Attribute catalog, people, projects, investigations, SEEK studies and one `GraphMeta` node. What the
graph holds, and every rule the writer enforces, is [`docs/neo4j-schema.md`](../../docs/neo4j-schema.md), sections
"v1.1: the graph_search target" and "v1.2: what the sync adds".

Nothing else writes the graph. Batch upload's stage 6 calls this package, every other NExtSEEK writer enqueues a row
for it, and the writers no NExtSEEK code sees (SEEK's Rails UI and REST API, Rails jobs, hand SQL, operator scripts)
are caught by a nightly pass that compares content rather than timestamps. Why it is built this way is the
[sync design](../../docs/superpowers/specs/2026-09-15-graph-search-sync-design.md); the graph itself was built for
the graph_search proof of concept
([POC design](../../docs/superpowers/specs/2026-09-14-graph-search-poc-design.md), section 6).

## Three layers, one code path

| Layer | When | Sees |
|---|---|---|
| Hooks | after every write NExtSEEK code makes | exactly what that writer changed: one outbox row, drained within seconds |
| The nightly targeted sync (`--reconcile`) | 02:00 UTC | every sample whose content differs from what its node was written from, however it changed |
| The weekly full sync (`--full`) | Sunday 03:00 UTC | everything, the graph-only nodes and the statistics included |

A hook never calls Neo4j inside a request: it writes one row to `graph_sync_outbox` after its own commit and
returns. The loop drains that row through the same functions the nightly and weekly syncs use, so there is one
projection, one writer and one deletion rule for every path.

## Surface

The management command `graph_sync`, plus the read-only status endpoint
`GET /nextseek_api/admin/graph-sync/status/` (`nextseek_api/services/graph_sync_status.py`, superuser, local and dev
only).

```
manage.py graph_sync (--loop | --once | --full | --catalog | --reconcile | --drift | --verify | --samples IDS
                      | --requeue-dead | --labels | --merge-studies [IDS] | --unmerge-studies PATH[,PATH...]
                      | --studies | --small-tables)
                     [--json] [--dry-run] [--chunk N] [--run-dir PATH] [--run-root PATH] [--interval S]
                     [--no-record] [--apply-label-changes] [--seed N] [--bench-keys FILE] [--kind KIND]
                     [--i-mean-the-live-graph]
```

| Mode | Does | Writes the graph |
|---|---|---|
| `--loop` | never returns: housekeeping, the schedule, then the drain, once every `--interval` seconds (default 5) | through its passes |
| `--once` | one pass of the loop | as above |
| `--full` | the whole ordered sync (`run.py`'s module docstring). Its preflight writes nothing and refuses before the first write on any problem it finds, except SampleType title conflicts alone: then it retires the samples MySQL lacks first and checks the titles once more | yes |
| `--catalog` | the SampleType and Attribute catalog only | yes |
| `--reconcile` | the nightly targeted sync: the catalog, the small tables, the map relabel, then the samples whose digest moved; a catalog refused for title conflicts alone is tried once more after the retire | yes |
| `--samples ID[,ID...]` | those samples, their lineage, their labels and their studies | yes |
| `--small-tables` | SEEK's small tables once (projects, investigations, people, memberships, every SEEK study's node), as an `isa` row and the nightly reconcile write them; `./startup.sh` runs it on local and dev right before the post-rebuild drift | yes |
| `--drift` | the reconcile's detection without its writes, the catalog comparison, gate G's structural checks and the freshness checks | no |
| `--verify` | gate G. `--seed N` fixes the seed of its random samples, so a run can be repeated | no |
| `--requeue-dead` | dead outbox rows back to pending, claimable at once; `--kind`, `--dry-run` | no |
| `--labels` | every DERIVED_FROM label against the rule, over the whole graph: new labels, renames and filled protocols written, the rest counted unless `--apply-label-changes` | yes |
| `--merge-studies [IDS]` | merge each SEEK study of the approval line (`id:kind`, as the dry run prints it), or give a legacy node its `seek_study_id` in place, journaled to `study_merge.tsv`; an id that reads another kind than its approved one stops the run; bare, `all`, or ids without kinds only with `--dry-run` (below) | yes |
| `--unmerge-studies PATH[,PATH...]` | reverse the merges journaled in these run directories or journals, and re-create the IN_STUDY links their `in_study_removed.tsv` archives hold; refused unless given every merge journal under the run root that names the same ids | yes |
| `--studies` | make every sample's IN_STUDY follow SEEK once, removing stale links whatever the switch says | yes |

Options that apply to more than one mode: `--json` puts only the JSON result on stdout and sends progress to
stderr; `--dry-run` makes `--full`, `--catalog`, `--reconcile`, `--labels`, `--merge-studies`, `--unmerge-studies`
and `--studies` read everything and write nothing, and `--requeue-dead` list what it would put back; every other mode
refuses it, exit 2, before connecting (`--samples`, `--small-tables` and a pass of the loop would write anyway); a
written `--full` and the three study modes make their own run
directory, `<kind>-<UTC time>` under the run root (`--run-root`, else `$GS_RUN_DIR`, else `graph_sync` under the log
directory), when `--run-dir` names none, and print it; `--chunk` is the page and transaction size; `--no-record`
leaves `graph_sync_run` alone; `--bench-keys FILE` (with `--full`) is a JSON list of attribute keys the index budget
must cover. `--apply-label-changes` is the operator's approval for label changes
(below).

**The live-graph rule.** The read-only modes (`--verify`, `--drift`) and the loop accept the stack's own `neo4j`
host. A hand-run `--full`, `--catalog`, `--reconcile`, `--samples`, `--labels` or study mode still refuses it
without `--i-mean-the-live-graph`, the dry runs of the last four included; the loop passes that flag to the children
it launches.

Exit status:

| Code | Means |
|---|---|
| 0 | success, and for `--drift` and `--verify` no failing check |
| 1 | a check failed, or a run failed part way (its report says where); or `--full`, `--catalog` or `--reconcile` could not take the graph-write lock, which another write held past its wait (the loop retries it); or a study mode stopped part way or found the lock busy; or `--unmerge-studies` ended `partial` |
| 2 | refused, and nothing was written: settings name no Neo4j URI; the live host without `--i-mean-the-live-graph`; a graph that is not at the writer's schema version (the reason is printed, so a CI step can skip); or a sync's preflight found a problem; an id `--merge-studies` does not act on, an approved id without its kind, or an id that reads another kind than its approved one before anything was written; two Study nodes sharing a `seek_study_id`; a path `--unmerge-studies` finds no journal in, or a merge journal under the run root naming its ids that it was not given; or a run directory a hand run cannot make; or `--dry-run` given to a mode that does not honour it |
| 3 | the run could not complete |

## The loop

`docker/scripts/entrypoint.sh` starts `manage.py graph_sync --loop` in a restart loop in the background, so nothing
about the sync can end the container. `NEXTSEEK_GRAPH_SYNC_LOOP=0` is the off switch and
`GRAPH_SYNC_RESTART_DELAY` the seconds between restarts. One pass does housekeeping (abandoned runs, expired
leases, old run directories), puts the slots the schedule owes into the outbox, and drains what it can claim. The
light kinds run in process; `full`, `reconcile` and `drift` run as child `manage.py graph_sync` processes, so their
memory returns when they end and a crash cannot take the loop with it. A claimed single-sample `samples` row takes up
to `writer.SAMPLE_CHUNK - 1` more such rows with it into one by-id sync (within the pass's 1,000 rows), whose outcome
closes, defers or fails every row it drained; a `batch:` row is one sync of its own. A row that has failed twice since
it was last written drains alone, so one sample whose sync raises cannot keep failing the rows merged with it (a
transient failure costs one merged retry); a row whose last failure was a gap traced to its own sample stays
mergeable, since a merged sync fails each gapped sample alone. While SampleType titles are held under other ids in the
graph (a type recreated in SEEK under its old title, which the nightly reconcile clears), the catalog sync is refused,
so a by-id sync leaves out the samples of a type that has no current SampleType node, writes the rest and names the
ones it left out. Only the rows holding those samples are deferred, not failed (a `batch:` row whole, a
`samples_of_type` row when any of its samples waited): no attempt is counted, so they never die and never show as
failing. They wait 30 minutes and stay mergeable, so the single-sample ones come back merged into one sync, beside the
writes made since, and never hold a fresh write back. A `catalog` row refused for those titles alone waits the same
way. A by-id sync that left a structural link unwritten (a type, a project, a study or an investigation link) fails
only the samples it names, each with why in its `last_error` (the project ids SEEK lacks, say): a single-sample row
fails on its back-off, and a row of many samples (a batch, a sample type) is closed and hands each such sample on as a
`sample:<id>` row that keeps its attempts, failing time and back-off; every other sample is done. A gap from SEEK's
data never heals by itself: fix the SEEK row, then
`--requeue-dead` the samples' rows if they died. A child's exit status decides its row: 0 and 2 (a refusal) are done,
anything else backs off, a busy graph-write lock (exit 1) included, except a `drift` child that exits 1 having saved a
result that reports drift: that check did its job, so its row is done and the drift is in its run record, never
retried into the same answer. The newest 20 run directories per kind are kept (the kinds the loop runs, and
`--small-tables`'s; a `merge_studies`, `unmerge_studies`, `study_links` or `catalog` directory is never pruned). Every
pass starts by closing all of the process's Django database connections: the loop lives for days and Django refreshes
connections only around a web request, so a connection MySQL dropped for idling would otherwise fail every drain on it
with "Server has gone away". Before this, the loop never recovered by itself: every pass failed with MySQL error 2006
until the process was restarted. A child run also ends with the same refresh, since it can hold the loop for hours.

| Cadence | When (UTC) | Fresh for |
|---|---|---|
| `reconcile` | 02:00 daily | 26 hours |
| `drift` | 02:30 daily | 26 hours (a run that ended `ok` or `drift`) |
| `full` | Sunday 03:00 | 8 days |
| the outbox | continuously | the oldest pending row: 1 hour |
| a failing outbox row, a failed full, reconcile, catalog or drift run | continuously | back-off plus 30 minutes from the first failure: 1 h 30 min, 6 h 30 min for a full sync |

A missed slot runs once at the next pass, not once per slot missed: the run it performs reads everything that
changed while the loop was down. A full sync satisfies a reconcile. The status endpoint reports those thresholds,
and the smoke suite fails a box that is outside them.

## The two dmac tables

Migration `0021_graph_sync_outbox_and_run` creates them. An instance that has not applied it still works: a hook
logs its failure and returns, and the status endpoint answers 503 rather than 500.
Migration `0023_graph_sync_outbox_failing_since` adds `failing_since`: the first failure since a row last succeeded,
kept across later failures, re-enqueues and deferrals, cleared when its work succeeds. The status endpoint's
`failing` part ages a row by it, because `enqueued_at` moves on every re-enqueue and `attempts` resets.

**`graph_sync_outbox`**, one row per unit of work. `(kind, key)` is unique, so repeated hook writes coalesce and a
scheduled slot is inserted once. A claim is a compare-and-set that counts an attempt; a lease that expires makes
the row claimable again; a failure backs off (6 hours for a full sync, an hour otherwise); a row at
`state.MAX_ATTEMPTS` is dead until a new write resets it, or `graph_sync --requeue-dead` puts it back once its cause
is fixed. A writer that cannot tell whether its write has landed yet enqueues with a delay (`delay_s`), which holds
the row back the way a back-off does. A successful full sync closes every row enqueued before it started, because it
read them all, except a row still inside its delay when the sync started: the sync may have read MySQL before that
write landed.

| Kind | Key | Enqueued by | The drain calls |
|---|---|---|---|
| `samples` | `sample:<id>`, or `batch:<name>` with the ids in `payload` | the sample hooks, batch upload stage 5, orphan resolution, assay registration, the publication backfill | `targeted.sync_samples` |
| `samples_of_type` | `type:<id>` | the attribute API, the legacy attribute editor, the sample-type proxy | `targeted.sync_samples_of_type` |
| `retire` | `sample:<id>` | the proxy destroy (delayed when SEEK did not answer), the legacy delete | `targeted.retire_samples`, which leaves an id MySQL still holds alone |
| `catalog` | `*` | the attribute API, the legacy attribute editor, the sample-type proxy, the clade admin | `run.catalog_sync` |
| `assay_map`, `protocol_map` | `*` | the internal-assay admin and the assay proxy; the SOP proxy | `targeted.relabel_for_maps` |
| `isa`, `membership` | `*` | the project, investigation and study proxies; the users API | `targeted.sync_small_tables` |
| `reconcile`, `full`, `drift` | `slot:<date or ISO week>` | the schedule | a child `manage.py graph_sync` process |

**`graph_sync_run`**, one row per run of `--full`, `--catalog`, `--reconcile`, `--drift` and `--samples`, with its
counts, its watermark and its status (`running`, `ok`, `failed`, `refused`, `abandoned`, or `drift` for a drift run
that found some). Writing it is best-effort: a run is never lost because its record could not be written.

## The graph-write lock

Every write unit (a full sync, a catalog sync, a reconcile, one drained row, batch upload's inline sync) holds
`GET_LOCK('nextseek_graph_write')` on the dmac connection, so no writer can add a sample between another run's
MySQL scan and its graph read. A full sync holds it from its preflight to its last write. A by-id sync waits at
most 60 seconds and otherwise returns `lock_timeout` without writing, which leaves its outbox row pending for the
next pass. On SQLite (the unit-test lane) the lock is a no-op behind the same function.

## Schema 1.2, and what the graph must be before anything is written

Every write unit reads `GraphMeta.schema_version` first and refuses, writing nothing, unless it equals
`writer.SCHEMA_VERSION` (`status: not_at_version`). A graph left at 1.1 therefore waits for the operator's first
`graph_sync --full --i-mean-the-live-graph`, and until that run an upload reports `graph: pending` and its outbox
rows keep the work. The loop never turns a graph into 1.2 by itself.

## Study nodes and IN_STUDY

What the graph holds is in `docs/neo4j-schema.md`, v1.2 "Study nodes and IN_STUDY"; the rule is in
`study_links.py` and the merge in `study_merge.py`.

**The switch.** `NEXTSEEK_GRAPH_SYNC_STUDY_LINKS` in the box's `docker/nextseek.env`. Unset, or any value but
`follow`, reads as `add`: every path adds the links SEEK holds and removes none, counting what it would remove
(`in_study_stale`). `follow` turns removal on in the drain and every other by-id sync, the full sync's SEEK studies
step and the reconcile's `study_links` step; `graph_sync --studies` removes whatever it says. A change takes effect
when the app container is recreated: `docker compose up -d --no-deps --force-recreate nextseek`. `rebuild` keeps it;
`install` re-renders `docker/nextseek.env` from the template, which carries the switch only as a comment, so a
reinstall turns it off. Turn it on only after, in this order: the box's merge dry run, the operator's approval of
its approval line, the merge, `--studies`, and then a second merge dry run. `--studies` gives every SEEK study its
node, so an id that read `legacy_only` now reads `merge` and needs its own approval and merge run, and
`id_collisions` grows by every SEEK study whose id a graph-only paper's node holds (never acted on). When that second
dry run's approval line is not empty: its approval, the merge, `--studies` again (nothing else rebuilds the links of
the samples that merge took off a paper), and another merge dry run. Turn the switch on only when a merge dry run's
approval line is empty: with `follow`, gate G's `12.studies.merge_candidates` fails on any id the merge would still
act on, and so does every later drift check and rebuild. While the switch is off, family
`12.studies` fails only on two Study nodes sharing a `seek_study_id`, so it stays green on a box that has not merged.

**Paper samples.** A sample on a graph-only paper study is not linked to the SEEK studies of its paper's own
investigation (the paper Study's one Investigation, matched to SEEK's by id and title, as the studies tool does), so
it is never counted twice there; its links to other investigations' studies are written. A paper whose investigation
cannot be matched withholds every link and is counted in `paper_investigation_unknown`: the curators' list.

**The merge.** `--merge-studies --dry-run --json` reports each SEEK study id's kind (`merge`,
`merge_other_investigation` and `rekey_in_place` act; the rest are reported), what each acting id's samples would do,
`id_collisions`, `legacy_only`, and `approval_line`: every id the merge acts on with its kind, as in
`3:merge,4:merge,6:rekey_in_place`. A `merge_other_investigation` id is in the line too, with that kind, and is also
listed apart in `merge_other_investigation` with any legacy Investigation it would leave empty. Without `--json` a list
prints as its count, so read the lists from the JSON. The operator approves the line as printed, or drops an id to
hold it back; `--merge-studies <approval line>` then merges exactly those, under the graph-write lock, journaling
every step to `study_merge.tsv` before its write. Each id's kind is read again under the lock, and one that reads
another kind than its approved one stops the run (exit 2 when nothing was written yet, else 1): run the dry run again
and approve what it prints. A rerun with the same `--run-dir` finishes a merge a crash stopped, held to the kind its
journal recorded; one that stops on such an id exits 1, not 2, since the earlier attempt may have written, and
`--unmerge-studies` with that run directory puts back what it moved.

`--unmerge-studies` takes, in one call and in any order, every merge run directory of the ids it undoes (a crashed run
and its rerun; for a whole box, the second approval's after `--studies` too; for an id merged, undone and merged
again, both merges', and it undoes the latest) and every run directory whose `in_study_removed.tsv`
should come back (a `--studies` run's, and after the switch went on the drain's, reconcile's and full sync's while
they are kept): the archived links come back before the sources move. It finds every merge journal under the run root
itself and refuses (exit 2) while one that names its ids is not given. It reports a journaled source whose element id
now names another node (`sources_replaced`, never written), a journal line a crash cut short, which it skips as the
merge does (`journal_lines_skipped`), and a sample that reached a merged study after the merge
(moved to the re-created seek-keyed node, `arrived_after_merge`), and ends `partial` (exit 1) when it refused an id,
could not restore an Investigation, or had to leave such a sample on a legacy node with no seek-keyed node to move to.
Its report is saved in its own `unmerge_studies-<UTC time>` run directory. Merge, unmerge and `--studies` directories
are never pruned.

**Paths that move a link.** The assay proxy enqueues an assay's members when it creates the assay or a PATCH sets
its study or samples (for a PATCH, the members it held before and the samples the request names, whatever SEEK's
answer says), keys `batch:assay:<SEEK id>:<time_ns>:<n>`; a proxy write SEEK may have committed without
confirming it (a 5xx, a timeout) enqueues its rows held back five minutes; the study proxy's `isa` row writes every
SEEK study's node with its title, description and Investigation; an assay moved in SEEK's own UI reaches the graph at
the next reconcile.

## DERIVED_FROM labels

The labels are batch upload's rule, moved here and fed from MySQL: the SEEK assays both endpoints share, resolved
through the internal-assay map with the smallest internal id winning and the SEEK assay as the fallback, and the
child's `Protocol` resolved by the house three-format rule. All five assay properties and the protocol pair are
written together, never a subset, and every edge graph_sync creates is labelled in the same run.

What is written without the operator's approval is a **new** label (an edge whose three singular assay fields are
all null, guarded in the Cypher itself; on such an edge a stored protocol is kept), a **rename** (the edge keeps its
assay ids and an internal assay title changed under them: `renamed`) and a **filled protocol** (a protocol where none
was stored: `protocol_filled`), the last two only where the stored values still equal the ones read, and on an edge
stored without plural lists only when the rule's list holds the edge's one internal assay. Every other
difference, any change of which assay an edge carries, is classified per edge (`new`, `equal`, `plural_missing`,
`renamed`, `protocol_filled`, `changed`, `cleared`), counted per property in the run's report (`labels_*`,
`labels_by_property`, `labels_examples`) and left alone. `--apply-label-changes`, or
`NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply` for the loop, is what writes the rest, and then only where the stored
values still equal the ones read. Read a run's label counts before turning it on. `graph_sync --labels` runs the
label step alone over the whole graph, for a backlog the by-id and nightly paths do not reach.

## Lineage

MySQL's parent tokens are the truth for DERIVED_FROM between Sample nodes, so the lineage step makes the graph equal
to them. Every declared pair the graph lacks is created and labelled; a declared edge that already exists keeps its
properties. Then every DERIVED_FROM between two Sample nodes that MySQL does not declare (a pair a later Parent edit
left stale, a self-loop, a token that no longer resolves to that sample) is written to
`derived_from_undeclared_archive.tsv` and deleted in batches; the file is in place before the first delete. An edge
touching an `OrphanSample` is left alone. Gate G check 1 fails on any undeclared edge still between two Sample
nodes, and check 9 on a declared edge whose endpoints share an assay and that carries no label.

## Deleting a sample

One rule, applied by `targeted.retire_samples` on every path. A `:Sample` graph_sync wrote (it carries `synced_at`)
whose id MySQL no longer holds is appended to `retired.tsv` and `DETACH DELETE`d. A `:Sample` graph_sync never wrote
becomes an `:OrphanSample`: `:Sample`, every `T_` label, `OF_TYPE` and `IN_PROJECT` removed, `orphaned_at` set, its
properties and its DERIVED_FROM kept, because those edges may be lineage MySQL never had. Existing
`:OrphanSample` nodes are left as they are.

## What the drift check compares

Three families, and they answer different questions.

- **Samples.** `samples.missing_in_graph`, `samples.not_in_mysql`, `samples.source_hash_mismatch`: MySQL's digest
  stream against the graph's `source_hash` values. `samples.new_uuids` is reported, never failed.
- **Catalog.** `catalog.sample_types` and `catalog.types_with_attribute_set_diff`: what MySQL declares against what
  the graph holds. `catalog.type_properties_differ`, `catalog.attribute_properties_differ` and
  `catalog.type_counts_stale` compare what the catalog sync writes besides titles, by id: a type's label and
  deprecated flag, an attribute's value type, required flag and position, and each type's stored sample count. Gate G's `3.catalog.*` checks the catalog against the graph's own
  sample nodes, which is internal consistency and cannot see that MySQL has moved; these are the other direction. Only the declared side
  is compared, because an Attribute with `declared` false is an observed key and a normal state.
  `stats.catalog.types_without_context` counts sample types with no `sample_types_context` row. It is a stat and
  never a check: a missing context row is legal, so any threshold would be invented, but the number makes a type
  that silently lost its curated card visible.
- **Freshness.** `freshness.full`, `freshness.reconcile`, `freshness.outbox` from the run records, so a stale or
  never-run sync fails; `freshness.readable` fails instead when those records cannot be read.

`./startup.sh rebuild` runs `--drift` afterwards and writes the result into the CI record's `## Graph drift`
section, naming any failing check.

## What a run writes

The run directory is `--run-dir`, else a new directory under `--run-root`, `$GS_RUN_DIR` or `<LOG_DIR>/graph_sync`.
A dry run writes no file.

| File | Written by | Holds |
|---|---|---|
| `full_sync.json` | `--full` | the report: every step's counts, `timings_s`, the label counts, `status` (`ok`, `refused` or `failed`) and `error`; written even when the run fails or is refused |
| `census.json` | `--full` | one entry per attribute key: sample type, title, value type, role, `declared`, samples with a value, longest value, cast failures |
| `child_of_archive.tsv` | `--full` | every CHILD_OF pair before CHILD_OF is deleted |
| `derived_from_undeclared_archive.tsv` | `--full`, `--reconcile`, `--samples` | every undeclared DERIVED_FROM between two Sample nodes before it is deleted, with its properties as JSON |
| `retired.tsv` | any path that retires | each retired node's id, uuid, type and incident-edge count, before the delete |
| `reconcile.json` | `--reconcile` | the detection counts, what each step did, and whether the guard tripped |
| `study_merge.tsv` | `--merge-studies` | the journal: per id its plan (both nodes' properties and Investigations), each moved source and whether it was only on the seek-keyed node, and `done`; appended and flushed before each write |
| `study_merge.json` | `--merge-studies` | the plan and the result |
| `in_study_removed.tsv` | any path that removes an IN_STUDY link | sample id, the Study's `seek_study_id` and `id`, the edge's element id and the path that removed it, before the delete |
| `study_links.json` | `--studies` | samples read and differing, links added, removed and withheld, paper samples, samples kept with no SEEK study, OrphanSample links |
| `sample_types_deleted.tsv` | the catalog step (`--full`, `--catalog`, the reconcile, the drain) | each SampleType node deleted because SEEK lost it and no Sample reaches it: id, title, label, attribute keys |
| `investigations_deleted.tsv` | the small tables (`--full`, the reconcile, the drain) | each Investigation node deleted because SEEK lost it and no Study holds it (only a Study node SEEK still has, or a graph-only paper Study node, holds one): id, title, project ids |
| `gate_g.json`, `catalog_sync.json` | `--verify`, `--catalog` | that run's report, with `--run-dir` |

## The modules

One concern each; `git ls-files nextseek_api/graph_sync` lists which have landed.

| Module | Holds |
|---|---|
| `projection.py` | pure: one sample row to its property map, type label, `search_text`, parent lists and `source_hash`; the value casts |
| `catalog.py` | pure: the SampleType and Attribute catalog from SEEK and the dmac context tables |
| `labels.py` | pure: the DERIVED_FROM label rule and the classification of a stored label against it |
| `label_check.py` | pure: canonicalising and comparing two label maps, for the verification against a graph dump |
| `schedule.py` | pure: which scheduled runs are owed at a given moment |
| `sources.py` | MySQL readers: keyset-paged samples, by-id readers, project and assay links, the digest stream, the resolved assay and SOP maps |
| `models_db.py` | the two dmac tables, re-exported from `nextseek_api/models.py` so Django registers them |
| `state.py` | the outbox, the run records and the graph-write lock |
| `health.py` | pure, standard library only: the judgement of a status body (stale jobs, dead and failing rows, failed runs, drift) that the smoke suite and `manage.py graph_sync_health` share |
| `hooks.py` | what every NExtSEEK writer calls after it writes; it never raises into its caller |
| `cypher.py`, `writer.py` | the Neo4j statements and the chunked writer |
| `targeted.py` | the by-id entry points: sync, retire, relabel, the small tables |
| `reconcile.py` | the nightly targeted sync |
| `loop.py` | one pass of the loop: housekeeping, the schedule, the drain |
| `drift.py` | the read-only drift check |
| `verify.py`, `run.py` | gate G and the ordered full and catalog runs |
| `study_links.py` | the IN_STUDY rule: the per-box switch, the streaming diff against SEEK, the rebuild step `--studies`, the full sync and the reconcile share |
| `study_merge.py` | the study merge: selection of a kind per SEEK study id, the dry-run plan, apply with its journal, undo |

## Who calls it

Every writer of a table the graph reads enqueues after its own commit, and the CI writer registry fails when one
does not. Batch upload is the exception that also syncs inline: stage 5 writes the outbox row inside each batch's
transaction and stage 6 calls `targeted.sync_samples` for the job's committed ids
([`nextseek_api/batch_upload/README.md`](../batch_upload/README.md)). The other hook sites are the native attribute
API, the legacy sample pages and attribute editor, the SEEK proxy and users ViewSets, the clade and internal-assay
admin pages, assay registration and the publication backfill command.

## Running and testing

The pure modules, the state machine, the hooks and the command are unit-tested in the Django unit lane
(`ci/README.md` "Running and testing"), test files `nextseek_api/tests/test_graph_sync_*.py`. Those globs block CI,
so a test added there fails a job from the commit that adds it. `./startup.sh rebuild` runs `--drift` afterwards and
writes a `## Graph drift` section into the CI record; the smoke suite reads the status endpoint
(`ci/smoke/test_graph_sync_status.py`).
Every `./startup.sh ci` and `rebuild`, production included, also asks `manage.py graph_sync_health` in the app
container, which judges the same body with `health.py` (`startup/README.md` "Graph sync health on every box").
A run against real data happens only in the throwaway lane, never against
the live stack: `scripts/graph_search/lane.sh app graph_sync ...` (see
[`scripts/graph_search/README.md`](../../scripts/graph_search/README.md)).

## Depends on / depended on by

- Depends on Django's `seek` and `default` database connections, the `neo4j` driver,
  `settings.NEO4J_DATABASE`, migration `0021_graph_sync_outbox_and_run`, the graph contract
  [`nextseek_graph/schema.py`](../../nextseek_graph/README.md) for every label, relationship, property, key and
  constraint name and the schema version it writes, and the rule it imports rather than copies:
  `nextseek_api/batch_upload/helpers.py` for Protocol-to-SOP resolution. Its tests compare its parent-identity and
  label rules with the frozen outputs of batch upload's former rule
  (`nextseek_api/tests/fixtures/graph_sync_batch_upload_parity.json`).
- Depended on by every writer that enqueues (above), by the lane scripts in `scripts/graph_search/`, by
  `nextseek_api/services/graph_sync_status.py`, and, through the graph it writes, by
  [`nextseek_api/graph_search/`](../graph_search/README.md).
