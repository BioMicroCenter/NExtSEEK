# Extending the graded set to 21 families — what changed and how to run it

**Goal:** take the 254 arms already graded by hand and *extend* them to cover the
missing task families, rather than rebuild the corpus and re-grade from scratch.

**Result:** selection 127 → 152, families 13 → 21, **all 254 human grades still
valid**, 50 new arms to run at roughly $6.

---

## 1. Why additive rather than a rebuild

The corpus-rework branch (`nessie-corpus-rework`) was briefed to produce a *clean*
corpus, and it did: 3 retired, 32 deselected, 25 edited, 21 promoted, 9 added,
1 renamed. That is the right answer to a different question. **Every retirement,
deselection and edit voids a human grade** that has already been paid for and
earned — its own accounting put 136 of 254 grades surviving.

This branch takes **only the additive half** of that work. The 127 as-run
variants keep their ids, their turn text and their `is_bayesian` flag, so every
one of the 254 `<variant_id>::<arm>` grades re-imports.

The build script asserts that as a precondition rather than trusting it: it
refuses if any as-run id leaves the selection or has its text changed.

| | before | after |
|---|---|---|
| selection | 127 | **152** |
| families covered | 13 | **21** of 24 |
| human grades still valid | — | **254 of 254** |
| new arms to run | — | **50** (25 variants × 2 engines) |
| estimated cost | — | **~$5.90** observed ($0.236/CC arm, measured) |

### What was added

**16 promoted.** Atlas variants — machine-generated on 2026-08-04 and quarantined
from `is_bayesian` by policy — that a human then read, ground-truthed and repaired.
This is the exit `corpus.curated` documents: *"promoting an atlas variant to
reviewed is a tag flip, not another round of re-measurement."* Reading them caught
defects a flag-flip would have shipped into a paid run: three had a multi-turn
script flattened into a single literal query string, one asserted 237 NHP samples
when 237 is the *mouse* count (NHP is 2), and one asserted `route_source == "baml"`
which forcing falsifies by construction.

**9 added.** Written fresh, ground truth verified against the live local stack.

Every addition asserts **ground truth on the reply** (`matches_re` against a real
count or uid) rather than NS-internal plumbing — `api_ok` does not move at all
across the whole change. That is deliberate: under forcing the harness strips
engine-internal criteria from a CC arm, so a plumbing assertion measures nothing
on half the study.

### What was deliberately left out

| family | why |
|---|---|
| `entity_write` (3) | asks the system to **create things**. If CC's write path fires, a paid run mutates the database. |
| `cross_session_memory` (2) | forcing pins both arms to one engine, so the CC→NS cross-*engine* recall behind issues #36/#37/#38 is unreachable this way. |
| `engine_routing` | by design: asserting a route the harness itself forced is tautological. |

That is why the count is 21 of 24 and not 24 of 24. All three are decisions, not
oversights.

---

## 2. Running the delta

> **Do not run `--bayesian` against the committed corpus.** It selects all 152 and
> would repay ~$30 to re-answer 127 questions a human has already graded.

`--bayesian` has no case-list flag, deliberately — `is_bayesian` *is* the
selection, because "accepting a second selection source would make what ran depend
on two things at once". So a delta run is done by narrowing the flag, running, and
putting it back.

```bash
# 1. narrow the selection to what has NOT been graded (152 -> 25)
python nessie_tests/scripts/delta_selection.py --graded nessie_bayes_full/grades.json

# 2. run the delta into its OWN directory — the paid manifest is never touched
python -m nessie_tests --bayesian \
    --base-url http://localhost:8000 \
    --user demo --password demopassword \
    --out ./nessie_bayes_delta \
    --max-usd 12

# 3. put the corpus back. The narrowing is a RUN-TIME state, not a commit:
#    the committed corpus is the whole 152-variant study.
git checkout nessie_tests/corpus.json
```

Needs a **staff** account — `force_route` is silently dropped for anyone else, and
the preflight refuses rather than measuring the router by accident.

`delta_selection.py` refuses to run against a dirty `corpus.json`, because
`git checkout` is its only undo and it will not bury someone's edit underneath.

### Then grade, then merge

```bash
# build the blind grading page for the 50 new arms
python nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py \
    --run ./nessie_bayes_delta --out ./nessie_bayes_delta/report_bayes.html
python3 -m http.server 8902 --directory ./nessie_bayes_delta

# grade all 50, download grades.json into ./nessie_bayes_delta/, then merge
python nessie_tests/output_skill_bayesian/merge_grades.py \
    --run ./nessie_bayes_delta --grades ./nessie_bayes_delta/grades.json

# combine both runs into one HiBayes-ready study
python nessie_tests/scripts/human_functional_rows.py \
    --run ./nessie_bayes_full --run ./nessie_bayes_delta \
    --out ./nessie_bayes_study
```

`human_functional_rows.py` writes the two per-arm files HiBayes actually consumes,
with the human grade as `functional_success`. It **errors** on a
`<variant_id>::<arm>` present in both runs rather than silently taking the last
one: two runs holding the same arm is a disagreement somebody has to settle.

Verified against the base run alone, it reproduces the shipped deliverable exactly
— 127 rows per arm, NS 103, CC 112.

---

## 3. Two things the tests now pin

**`_ROUTE_EXPECTATION_UNSETTLED`** (`tests/test_corpus.py`). The 8 new families
have no `route_policy.families` entry, so no route criterion is injected — 20 of
the 25 additions carry none. `route_policy` is operator-settled ("Settled by the
operator 2026-07-28: bulk export is CC's job"), and guessing an engine for a family
nobody has ruled on would turn a free-tier run red while asserting something no one
decided. The gap is pinned as an **exact set**, so a new family cannot join the
corpus without a route expectation and go unnoticed. It does not affect the paired
run these families were added for: `--bayesian` forces the route and strips route
criteria on both arms.

**To close it:** add each family to `route_policy.families` in `corpus.json` and
drop it from the set.

**`_ADDED_2026_08_06`** (`tests/test_floor_ops.py`). The floor measurements are
evidence about the floor *as it met a corpus somebody had already written*.
Variants added afterwards are excluded, exactly as the atlas set already is —
folding them in would leave every assertion passing while quietly making it
evidence for a different corpus.

---

## 4. Still open

- **The route expectation for 8 families**, above. Operator call.
- **One human grade looks wrong and inverts a product finding.** The rework branch
  argues `tree.cel_descendants::cc` was failed ("11 vs 13") when CEL-250319WHI-1-PUB
  has exactly 11 transitive descendants, and NS's 13 counted the sample itself plus
  its ancestor — i.e. **NS answered "associated" to a question that asked "derived
  from"**, and an NS defect was recorded as a CC failure. Not independently verified
  against the database here.
- **Two selected variants point at UIDs that do not exist** —
  `report.build_a_pride_deposit_for_d_ms` and `pipeline.happy_path_scrnaseq`. Both
  are in the as-run 127 and are kept, because editing them voids their grades. The
  PRIDE one is a *silent* bad question: both graders passed it, since its only
  criteria are satisfied by a lookup failure.
- **Eight false greens are one taxonomy defect.** `write.*_must_confirm_first`
  variants expect confirm-then-write but sit under `writes_unsupported`, so NS
  correctly refusing reads as a fail while CC's arm built an update workbook and was
  graded pass. The fix depends on whether "NExtSEEK writes are read-only" is still
  policy.
- The rest of `nessie-corpus-rework` — its retirements, deselections and edits —
  is untaken, not rejected. It is the right change to make *after* the grades those
  variants carry are no longer the thing being protected.
