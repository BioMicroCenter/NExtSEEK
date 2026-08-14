---
name: nessie-bayes-report
description: Build the split blind-grading report for a nessie_tests --bayesian run, grade it, and merge the human grades with Stage C's into the HiBayes table. Use when a paired dual-route run has finished and needs evaluating.
---

# The paired run's split report

A `--bayesian` run drives every selected corpus variant through **both** engines,
interleaved, and records the pair. This skill turns that run into one HTML page a
human grades every answer in, and then joins those grades to the LLM grader's.

**Every answer is graded twice on purpose.** The whole point of the paired design
is to say whether the LLM grader can be trusted on this corpus, and that question
only has an answer if the human grade is independent of it. So the page is
**blind**: Stage C's verdict ships inside the file and is not rendered for a row
until that row carries a human grade. Do not defeat this, and do not "just peek"
at `stage_c.json` while grading. Anchoring is not a small effect, and once the
pass is anchored the disagreement set means nothing and cannot be recovered
without regrading.

## The sequence

```bash
# 0. the paid run (see nessie_tests/README.md; this is the expensive step)
python -m nessie_tests --bayesian --out ./nessie_bayes_out

# 1. pull the artifacts the run left behind, into ./nessie_bayes_out/artifacts/
python -m nessie_tests.collect --run ./nessie_bayes_out
#    Reads through the RUNNING `nextseek` container: task rows and transcripts
#    out of its Django ORM, trees out of `docker cp`. Add `--host <box> --user
#    <acct>` for a run executed on the dev box; the default is the local daemon.
#    `zstandard` is the one non-stdlib thing this step needs on THIS side (the
#    transcripts arrive compressed); without it every CC arm's session.jsonl is
#    recorded unreadable, and the step says so before it starts. No install:
#      uv run --no-project --with zstandard python -m nessie_tests.collect --run ./nessie_bayes_out

# 2. the HiBayes CSVs (also decides which arms are EXCLUDED, see below)
python -m nessie_tests.export --run ./nessie_bayes_out        # writes hibayes_eval_rows_{ns,cc}.csv,
                                                              # hibayes_functional_eval_inputs.csv,
                                                              # excluded.csv, unobserved.csv,
                                                              # arm_diagnostics.csv

# 3. the blind report
python nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py \
    --run ./nessie_bayes_out --out ./nessie_bayes_out/report_bayes.html

# 4. GRADE IT. Open it in a browser and work through every arm.
python3 -m http.server 8901 --directory ./nessie_bayes_out
#    ... then download grades.json when the bar reads n of n.

# 5. the LLM grader, in the dmac-assistant repo
#    Stage C reads hibayes_functional_eval_inputs.csv and writes stage_c.json,
#    keyed "<variant_id>::<arm>" -- the SAME keys grades.json uses.
cp <dmac-assistant stage C output> ./nessie_bayes_out/stage_c.json

# 6. rebuild the report with the verdicts in it, and reveal
python nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py \
    --run ./nessie_bayes_out --out ./nessie_bayes_out/report_bayes.html
#    Reload, re-import grades.json if the browser store was cleared, press
#    "Reveal all" (it unlocks only once every gradable arm is graded), and read
#    the Disagreements filter. That set is the output of this whole exercise.

# 7. the joined table
python nessie_tests/output-skill-bayesian/scripts/merge_grades.py \
    --run ./nessie_bayes_out --grades ./grades.json --out ./nessie_bayes_out/graded_rows.csv
```

`fetch_run.py` is **not duplicated here**. Pull the run with the sibling skill's
copy, unchanged — it handles both transports:

```bash
# run executed on the dev box (the default target is fairdata-dev)
python nessie_tests/output-skill/scripts/fetch_run.py --out ./nessie_bayes_out ...

# run executed on THIS workstation -- pass an empty --host to go straight to the
# local docker daemon. `ssh localhost` is not a fallback; there is no sshd.
python nessie_tests/output-skill/scripts/fetch_run.py --host "" --out ./nessie_bayes_out ...
```

The local form is the one a `--bayesian` run needs today, because the container
this pipeline was built against runs on the workstation.

## Step 1: what the collection actually reads

`nessie_tests/collect.py` takes its sources by INJECTION --
`collect.collect(manifest, out_dir, sources)` -- and
`nessie_tests/sources.py::DockerSources` is the one concrete implementation.
Everything goes through the running `nextseek` container, so **no database
credential ever reaches the host and nothing is added to the host test lane**:

| method | how it is served |
|---|---|
| `task_rows(task_ids) -> {task_id: row}` | one batched `QueryTask` query through the container's own Django ORM: `status`, `progress`, `result`, keyed by the caller's own id strings |
| `cc_transcript(session_id) -> bytes \| None` | the `CCSessionTranscript` rows for that **NExtSEEK `ChatSession`** (not `cc_session_id`), ordered by `created_at`, folded into one session `.jsonl` and recompressed to a single zstd frame |
| `copy_tree(src, dest) -> bool` | `docker cp <container>:<src> -` streamed out as base64 and unpacked here; **raises `collect.CopyFailed`** when the copy mechanism broke, returns `False` when the source is simply absent |

That split in `copy_tree` is not decoration: "cc_sweep reaped the scratch" is a
fact about the run and "docker is not running" is a fact about the collector, and
a hundred of the second must not read as a hundred of the first. The outcome is
**not** read off an exit code, because `docker exec <missing container> true` and
`docker exec <live> test -e <missing path>` both exit 1; a probe inside the
container echoes which of the three it is.

Two things about the transcript that are easy to get wrong and silent when you
do, both settled against the live database rather than assumed:

* `turn_id` is `str(query_task.task_id)` -- a random UUID in a `CharField` -- so
  ordering by it is a lexicographic **shuffle**. `created_at` is the real order.
* Each row holds the **cumulative** session `.jsonl`, not that turn's delta:
  `--resume` keeps Claude appending to one file under the per-session cc-state
  dir and every turn re-reads all of it. Turn 2's blob `startswith` turn 1's,
  byte for byte, on every multi-turn session in the database. So concatenating
  the rows duplicates every earlier turn. `sources.merge_transcripts` folds by
  containment, which is right for cumulative rows AND for the disjoint rows a
  wiped cc-state store produces.

**The command refuses rather than half-collecting.** No paired manifest, no pairs
in it, or a container that cannot answer a ping, and it exits 2 having written
nothing. That last one matters most: a collection run against a dead container
finishes and finishes LOOKING complete, with every artifact recorded missing --
indistinguishable on the page from a product that produced nothing, and
discovered, if at all, after grading 254 blank arms.

**If you skip step 1, everything downstream is degraded rather than broken, and
each step says so when it runs.** Steps 2 and 3 print the SAME warning
(`export.no_artifacts_warning`, one voice, one place) with their own tail: with
no `artifacts/` tree neither can see a deadline abort at all -- the only evidence
is a collected, still-non-terminal task row -- so a turn that blew the deadline
is indistinguishable from one that answered. On top of that the export's
`tool_calls_total`, `artifact_count` and **`final_answer`** are all absences
rather than measurements, which means Stage C would grade blank answers; and the
report renders every arm with "No reply was recorded". Neither is a run you can
read as measured.

## One artifacts tree, derived once

`export` and the report builder MUST read the same collected tree, and they do:
both call `collect.artifacts_dir(run)`, which is `<run>/artifacts` and is owned by
the module that writes it. Do not pass a different `artifacts_dir` to one of them,
and do not call `export.export(...)` from a Python shell without it.

The reason is specific. The page decides which arms carry grade controls with
`export._exclusion`, and a **deadline abort is visible only in the collected task
row**. Export over a different tree (or over none, which is what
`export.export(manifest, out)` in a shell does) and that arm gets a scored CSV
row while the page bands it ungradable -- so `merge_grades` raises
`IncompleteGrading: ... 'deadline.pair::cc'. Finish the grading pass...`, which
instructs the operator to grade a row the page gave them no way to grade.
`test_the_export_and_the_report_agree_on_which_arms_are_gradable` pins it.

## Grading, in practice

Three hundred grades is a real human pass, so the page is keyboard-driven:

| key | does |
|---|---|
| <kbd>j</kbd> / <kbd>k</kbd> | move to the next / previous gradable arm |
| <kbd>1</kbd> / <kbd>2</kbd> | grade the focused arm pass / fail, then advance |
| <kbd>n</kbd> | open the focused arm's note |
| <kbd>Esc</kbd> | leave the note |
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | download `grades.json` |

Pressing the same grade twice clears it. Every grade carries an ISO timestamp, so
a drift in standards over a long pass is at least measurable afterwards rather
than merely suspected.

Grades autosave to `localStorage` under `nessie-bayes-grades:<run_id>`, scoped to
the run so two reports graded in one browser cannot share a bucket. **That store
is not a backup** -- download `grades.json` when you stop, and use Import to
resume on another machine.

Grade the **answer**, not the engine. The arms are labelled because a trace is
unreadable without knowing which engine produced it, but "CC used more tools" is
not a reason to fail an answer that is correct and complete.

## Ungradable arms

An arm banded amber carries no grade controls. `export._exclusion` keeps three
classes of arm out of the runtime CSVs, because in none of them did the engine
answer the question it was asked:

| cause | what happened |
|---|---|
| `provider_outage` | every provider in the fallback chain 503'd; no product code ran |
| `never_executed` | an unset `requires_env` returned before the request was issued |
| `deadline_abort` | the turn blew `full_timeout_s` and its task row is still non-terminal |

The report computes this with **the same predicate**, so an arm the model never
scores is never an arm the human grades. They are still shown, and counted in
their own tiles outside the progress denominator: a report that hid forty
unobserved arms would show a complete pass over a run that never observed them.

A greyed arm with no band is different again -- that pair is half-written, which
is what an interrupted run looks like, and it is not a failure of anything.

## `arm_diagnostics.csv`: why a turn ended

The 14- and 12-column tuples are locked to an upstream contract, so two facts a
run observes have nowhere to go in them. They go in a third sidecar, keyed
`(query_id, arm)` like `excluded.csv` and `unobserved.csv`, with one row for
**every** arm the manifest holds -- excluded arms included, because those are the
ones whose error text matters most.

| column | what it is |
|---|---|
| `error_text` | the turn's own message, verbatim, from `result.error` or the `query_error` event |
| `error_class` | `provider_outage` / `usage_policy` / `timeout` / `unclassified`, plus `no_text` (it errored and left no message), `none` (rows were read, it did not error) and `unobserved` (no rows to read) |
| `stop_reason` | the last `stop_reason` in the collected CC transcript, empty unless `stop_reason_status` is `observed` |
| `stop_reason_status` | `observed` / `no_transcript` / `not_recorded` / `unreadable` / `not_applicable` (an NS arm) |

`usage_policy` is a **classification, not a ruling**. Whether an arm Claude Code
refused under the Usage Policy should be excluded like an outage or scored like a
failure is an open question and `export._exclusion` is untouched by it -- but the
run of `advanced.bacteria_mtb` that raised the question exported
`is_error=false, answer_provided=true, runtime_success=true, failure_mode=none`
over an arm that produced nothing, and the refusal message reached no file at all.

`error_class` can never contradict `is_error`: it takes the flag from the same
`runtime_flags` call the CSV row is built from, and `none` is the only token that
asserts the arm was fine. An arm that errored without a recoverable message is
`no_text`, and an arm with nothing collected is `unobserved` — which asserts
nothing, so it cannot contradict anything either.

`stop_reason` is the last one **in what was collected**, and is a floor rather
than the turn's final word: the transcript store holds the session file as of each
turn and the tail of a turn is often not in it, which is why most CC arms read
`tool_use` rather than `end_turn`.

## The grades.json contract

`merge_grades.py` reads exactly this, and nothing else:

```json
{"<variant_id>::<arm>": {"grade": "pass", "note": "", "ts": "2026-08-04T21:48:05.107Z"}}
```

`<arm>` is `ns` or `cc`; the key form is `export.stage_b_query_id`, which is also
how Stage C keys `stage_c.json`, which is what lets the two be joined at all.
`grade` is `"pass"` or `"fail"` and nothing else -- `merge_grades` treats any
other value as ungraded and **refuses to emit a table**, because a quietly
shorter one is how a partial grading pass gets read as a complete one. The page
pins all of this in `GRADE_CONTRACT` and builds its keys from it;
`tests/test_bayes_report.py` pins `GRADE_CONTRACT` against `export` itself.

## Files

```
scripts/build_bayes_report.py     paired manifest + artifacts + stage_c.json -> the page
scripts/merge_grades.py           a THIN entry point; the logic is the module below
templates/report_bayes.html.tpl   the page: three columns per question, blind gate, autosave
```

`merge_grades`'s logic lives in the importable package
`nessie_tests/output_skill_bayesian/merge_grades.py` (underscores), under test in
`nessie_tests/tests/test_merge_grades.py`. A hyphenated directory is not a Python
identifier, so nothing under `output-skill-bayesian/` can be imported, and a
script that cannot be imported cannot be unit tested -- both scripts in the
sibling `output-skill/` rotted for exactly that reason. The script here exists
only to give this file a path to name. Change behaviour in the package, not in
the script.

The other four modules the sequence uses are plain `nessie_tests` modules, not
skill files: `nessie_tests/collect.py`, `nessie_tests/sources.py`,
`nessie_tests/export.py` and `nessie_tests/bayes_manifest.py`.

`build_bayes_report.py` takes `--run` (the run directory), `--out`, and
optionally `--corpus` (defaults to `nessie_tests/corpus.json`, which is where the
questions come from) and `--fragment` (drop the `<head>` skeleton, for the
Artifact publisher; the default is a complete document, because this page is
opened and graded rather than published).

The paired manifest is always named through `bayes_manifest.MANIFEST_NAME`. A
normal run's `manifest.json` is a different schema that validates as an **empty**
paired manifest rather than raising, and two reproduced data-loss defects on this
branch came from that collision, so the builder refuses a directory that has the
one and not the other rather than reporting a run of nothing.

## Checking the layout after a template change

Both house-CSS traps in this template are invisible when you read the CSS and
obvious when you measure the page, so measure it:

* `.reply` carries a 78ch clamp in the sibling report. In a half-width column
  that leaves the one block being graded as the narrowest thing on the page, so
  it is overridden here. Check `.reply` width against its column's.
* Nested grid rows shrink to fit unless told otherwise, which leaves the shorter
  arm floating and puts the two grade bars at different heights on every card.
  Check that both `.arm` children of a `.pgrid` report the same height.

Serve the run directory, open `report_bayes.html`, and confirm both arms render
full-width in their columns, no row collapses, and `document.documentElement.scrollWidth`
does not exceed `window.innerWidth`. Screenshot it into the run directory as
evidence.
