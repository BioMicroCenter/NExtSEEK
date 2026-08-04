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

# 1. pull the artifacts the run left behind
python -m nessie_tests.collect --run ./nessie_bayes_out       # writes artifacts/ + collection.json

# 2. the HiBayes CSVs (also decides which arms are EXCLUDED, see below)
python -m nessie_tests.export --run ./nessie_bayes_out        # writes hibayes_eval_rows_{ns,cc}.csv,
                                                              # hibayes_functional_eval_inputs.csv,
                                                              # excluded.csv, unobserved.csv

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

`fetch_run.py` is **not duplicated here**. When the run is on the dev box rather
than local, pull it with the sibling skill's copy, unchanged:

```bash
python nessie_tests/output-skill/scripts/fetch_run.py --out ./nessie_bayes_out ...
```

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
scripts/merge_grades.py           grades.json + stage_c.json + the CSVs -> graded_rows.csv
templates/report_bayes.html.tpl   the page: three columns per question, blind gate, autosave
```

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
