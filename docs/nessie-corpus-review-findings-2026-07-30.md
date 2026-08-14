# Corpus review findings: issue #35

**Date:** 2026-07-30
**Scope:** all 548 questions (447 corpus turns / 381 variants, plus 101 real ad-hoc).
**Method:** mechanical. Every claim below is checked against the live Neo4j and
`seek_production` MySQL on the running stack, or against the observed payloads of the
2026-07-29 seed-0 run. Nothing here is inferred from a pass rate.

The local stack was confirmed to hold the **same data as the dev box** before anything
else: MetNet = 10 studies, MUS in Impact = 705, NHP = 408, tissue+cell in Impact = 10,688.
Those are the same values the 07-28/07-29 triage used as ground truth.

---

## The headline

> **29 of 447 turns (6%) can catch a wrong answer.**

Everything else asserts either plan shape (the query the model wrote) or observability
(that *something* came back). The 2026-07-28 family-floor rewrite fixed the right problem
in the right way, but it deliberately asserts *observability, not value*, so it moved 343
turns from "blind" to "sees that a number exists" and none of them to "knows what the
number should be".

| tier | what it asserts | turns | variants |
|---|---|---:|---:|
| A | something a wrong answer would violate | 29 | 29 |
| B | only that a result / reply exists | 343 | 325 |
| C | plan shape or route only | 75 | 27 |

This is the single number worth fixing. It is also why two rounds of triage went into
adjudicating assertions rather than the product.

---

## Axis 1: premise (is the question answerable at all?)

### Verified false premises

| entity | verdict | evidence |
|---|---|---|
| `GBM` | **absent** | 0 rows in `Investigation`, 0 in `Study`, 0 samples where `json_metadata LIKE '%GBM%'` |
| `zebrafish` | absent **by design** | 0 samples; these are the honest-zero cases and are correct as written |

**26 variants name GBM** (19 `graph_query`, 5 `search_advanced`, 1 `routing_graph`,
1 `reporting`). Only **4** are tagged `no_floor`, so the other 22 carry a floor that a
correct zero can still satisfy today, but their per-case criteria were written expecting rows.

This is 7% of the corpus asking a question whose only correct answer is zero. Three of the
six failures in the 07-29 run were these, and all three were initially scored as product defects.

### Premise notes that change a disposition

- **`Lab` exists.** The 2026-07-29 handoff recorded "zero rows in the entire database have a
  `Lab` key in `json_metadata`". That is **false**: **5,715 samples across 15 sample types
  carry `Lab`**, with 24 distinct values (`Fortune` 1589, `Plata Lab` 1071, `Fortune Lab` 964,
  `Alter Lab` 601, … `Essigmann` / `Essigmann lab` / `Essigmann Lab` / `ESSIGMANN_lab` all
  separately). `Lab` is absent from `TIS` and `D.SEQ` specifically, which is why the D.SEQ
  case saw none. The reply "none of the 258 records contain an explicit Lab field" was correct
  *for that result set* and wrong as a statement about the database.
- **`Organism` is NULL on all 50,887 samples.** The ad-hoc question "…save the UIDs of any
  that are missing an organism value" therefore returns everything. Degenerate, not wrong.
- **`Kamm` is a lab, not a project.** `refrec.refine_to_female` asks for "mouse samples in
  the Kamm project". There is exactly one project (`Published Data`, id 1); the six
  investigations are CSBC, Griffith, Impact, MetNet, SRP, Shoulders. The overlay already
  encodes this for the reporter (`criterion_rewrites.reporter_project_labs`), but the
  question text still says "project".
- **`Joanne Flynn` is stored two ways**: `JoAnne Flynn` (14,104) and `Joanne Flynn` (325).
  Both real. A test asserting either exact spelling is asserting a data-quality accident.

---

## Axis 2: top-level routing (the gap the issue does not yet cover)

> **405 of 447 turns (339 of 381 variants) assert no top-level route at all.**

Every route assertion in the corpus today comes from three places: the 7-case
`nessie_route` gate, the 26 variants the `route_policy` block rewrites, and a handful of
hand-converted cases. The other 89% of the corpus asserts an NS-internal `parser_plan.mode`
and is therefore blind to the router, the component that runs first on every turn.

The fix is **not** 339 hand edits. `route_policy` in `overlay.json` already applies a route
assertion structurally by family; it just only lists two families. Extending its `families`
map covers the whole corpus in one reviewable block.

### Per-family route decision table

`observed` = what the 2026-07-29 seed-0 run actually routed, as corroboration.

| family | turns | asserts route today | proposed | observed 07-29 | route_capabilities task family |
|---|---:|---:|---|---|---|
| graph_query | 59 | 0 | `nextseek_query` | NS x6 | lineage_or_graph |
| search_advanced | 67 | 3 | `nextseek_query` | NS x7 | sample_search |
| search_retrieve | 20 | 0 | `nextseek_query` | NS x2 | sample_search |
| search_parents_by_child | 18 | 0 | `nextseek_query` | NS x2 | sample_search |
| search_tree | 16 | 0 | `nextseek_query` | NS x2 | lineage_or_graph |
| routing_graph | 1 | 0 | `nextseek_query` | NS x1 | lineage_or_graph |
| routing_lab | 2 | 0 | `nextseek_query` | NS x1 | sample_search |
| system_question | 27 | 0 | `nextseek_query` | NS x3 | system_or_catalog_question |
| reporting | 64 | 0 | `nextseek_query` | NS x6 | report_generation / reporter_summary |
| refine_and_recall | 95 | 0 | `nextseek_query` | NS x5 | memory_lookup |
| pipeline_nfcore | 35 | 1 | `nextseek_query` | NS x2 | pipeline_build_and_launch |
| nessie_green | 4 | 3 | `nextseek_query` | NS x1 | sample_search |
| nessie_repro | 6 | 2 | `nextseek_query` | NS x1 | lineage_or_graph |
| unsupported | 7 | 7 | `container_cc` (2 overrides) | unrelated x1 | open_ended_analysis / unrelated |
| writes_unsupported | 19 | 19 | `container_cc` | CC x2 | entity_write_via_api / batch_upload |
| nessie_route | 7 | 7 | per-case | unrelated x1 | this family IS the gate |

Every family default above is corroborated by what the run actually did. **No family
default contradicts observed behaviour.** That makes this a low-risk change.

### Exceptions: cases inside an NS family whose shape argues for CC

These nine are the only ones where the family default is not obviously right:

| case | family | why it argues CC |
|---|---|---|
| `refrec.ah_got_it_retry_that_search_wi` | refine_and_recall | seed is `Create me investigation "TEST WOW TEST"` |
| `refrec.can_you_run_that_again_but_wit` | refine_and_recall | seed is an investigation write |
| `refrec.try_it_again_but_with_project` | refine_and_recall | seed is an investigation write |
| `refrec.try_that_again_but_with_projec` | refine_and_recall | seed is an investigation write |
| `refrec.try_that_previous_request_but` | refine_and_recall | seed is an investigation write |
| `report.export_d_msp_230828gri_4_pub_f` | reporting | "Export … for submission to PRIDE" |
| `pipeline.build_a_single_cell_rna_seq_pi` | pipeline_nfcore | "Build a … pipeline CSV" |
| `route.cc_reingest`, `route.cc_write_investigation`, `route.cc_open_ended_analysis` | nessie_route | already correct, listed for completeness |

The five `refrec.*` ones are a real defect, not a route nuance. See below.

---

## Axis 3: are the PASS verdicts accurate?

Independently recomputed every checkable count from the 2026-07-29 seed-0 run against the
live stores. **Sixteen passes are accurate.** Three are not.

### Confirmed accurate

| case | observed | ground truth |
|---|---:|---|
| `graph.what_mice_are_in_the_impact_st` | 705 | 705 MUS in Impact |
| `graph.find_me_studies_in_metnet` | 10 | 10 studies in MetNet |
| `graph.tissue_cell_impact` | 5000 of 10,688, truncation disclosed | 10,688 |
| `graph.what_studies_have_monkeys` | 11 | 11 studies with NHP samples |
| `graph.what_projects_have_samples_tha` | 5 | 5 investigations via Short Read Sequencing |
| `repro.cypher_uid_dot` | 6 | 6 D.SEQ descendants of those two NHPs |
| `pbct.find_me_monkeys_that_have_imag` | 104 | 104 NHP with both D.IMG and D.SEQ |
| `pbct.find_me_monkeys_that_have_both_2` | 56 | 56 NHP with both D.FLOW and D.SEQ |
| `advanced.find_me_mice` | 1000 of 1179 | 1179 MUS |
| `advanced.what_proteomics_data_exists_in` | 173 | 118 D.MSP + 55 A.MSP |
| `advanced.find_me_all_samples_associated` | 28 | 28 AB samples mentioning CD8 |
| `routing.lab_ooc_kamm_count` | 530 | all 530 OOC samples are Kamm-lab |
| `advanced.zero_result_zebrafish` | 0 | 0 |
| `refrec.of_those_mice_can_you_summariz` | 195 | 195 NDMA-treated mice |
| `refrec.refine_those_results_to_cd8_de` | 408 → 46 | 408 NHP; 17 CD8a + 14 CD8b + 15 "CD8 Depletion" = 46 |
| `routing.gbm_study_count` | reply "0 samples" | 0 |

### Masked passes: scored GREEN, output is wrong or unverified

1. **`advanced.show_me_analyzed_sequencing_da` is off by one.** Returned **222**; the five
   requested types total **223** (A.GEX 13 + A.ALN 32 + A.SCXP 166 + A.CHRM 12; A.VCF does
   not exist as a sample type). Passed because its only value criterion is `row_count gte 1`.
2. **`retrieve.metadata_filter` returns 7 rows for 1 requested UID.** The body is
   `{"identifiers": ["D.SEQ-230512FOR-288-PUB"]}`; exactly one sample carries that title and
   nothing shares its stem. The reply says so plainly: "returned 7 matching records … the
   primary requested record is …". Passed on `api_ok` + `api_outcome_observed` alone.
   Whether 7 is correct depends on the intended semantics of `/admin/samples/retrieve/`.
3. **`cons.nhp_sequencing_engine` xpasses on a wrong answer.** Both turns returned **408**,
   which is every NHP sample in the database. The consistency group asserts only that the two
   agree, so agreeing on the wrong number scores xpass. See the issue-#33 note below.

### Failures where the product was right

- The three GBM cases returned 0, which is correct.
- `green.refine_recall` returned **2** for `filter_matchType: EXACT, filter_searchText: "4 week"`.
  Ground truth: `Cohort = '4 week'` on exactly **2** samples. The query asked was answered
  correctly. But `Cohort = '4wk'` covers **237** more. See the open question below.
- `sys.what_kinds_of_reports_can_i_ge` is a genuine defect: 601s, then an empty reply.

### One real, newly quantified product defect

`refrec.of_those_which_are_actually_fr_2` searched D.SEQ + free text `SHA` and got **258**.
Ground truth: **255** D.SEQ samples carry `SHA` in the UID; 3 more match only because `SHA`
appears inside a filename (`D.SEQ-231101GRI-2-PUB` has `200701Sha_…`). **Three false
positives, 1.2% precision loss.** The case failed, but on a reply-prose criterion, not on this.

---

## Axis 4: structural defects the issue has not yet named

### Broken conversations (9 variants): the seed refers to state that never existed

These read as a sliding window over a real chat log: turn *N* became the seed and turn *N+1*
the follow-up, so the seed inherited anaphora it can no longer resolve.

| variant | turn 0 | turn 1 |
|---|---|---|
| `refrec.and_how_many_dna_samples_did_w` | "How many D.SEQ samples did the very first search in this session return" | "And how many DNA samples did we find" |
| `refrec.how_many_distinct_labs_are_rep` | "Of those, which are actually from the SHA lab? …" | "How many distinct labs are represented across **those 264 samples**" |
| `refrec.just_show_me_the_first_3_uids` | "Of those, which ones are from the SHA lab specifically? …" | "Just show me the first 3 UIDs from that search" |
| `refrec.of_the_ndma_mice_which_are_mal` | "What about those NHP samples, how many were there" | "Of the NDMA mice, which are male" |
| `refrec.of_those_which_ones_are_from_t` | "How many were returned in that search" | "Of those, which ones are from the SHA lab …" |
| `refrec.what_about_those_nhp_samples_h` | "How many D.SEQ IMPACT samples did the first search return" | "What about those NHP samples …" |
| `pipeline.build_me_an_nf_core_sampleshee_2` | "Build me an nf-core samplesheet for **those mice**" | (single turn) |
| `pipeline.build_me_an_nf_core_sampleshee_3` | "Build me an nf-core samplesheet for **those**" | (single turn) |
| `pipeline.build_me_an_nfcore_samplesheet` | "build me an nfcore samplesheet for **those samples**" | (single turn) |

`refrec.how_many_distinct_labs_are_rep` additionally hardcodes **264** into the question,
pinning it to one historical run.

Four of these are also **topic mismatches**, the follow-up asks about a subject the seed
never searched (D.SEQ → DNA, NHP → NDMA mice, D.SEQ → NHP).

### Write seed, NS follow-up (5 variants)

`refrec.ah_got_it_retry_that_search_wi`, `…can_you_run_that_again_but_wit`,
`…try_it_again_but_with_project`, `…try_that_again_but_with_projec`,
`…try_that_previous_request_but`.

Each seeds with `Create me investigation "…"`, which the settled policy routes to
container_cc, then asserts `parser_plan.mode eq refine_last_search` / `ask_about_last_results`
on the follow-up. No NS bundle can exist, so the criterion resolves to `None` and fails
unconditionally. This is the same defect class the `route_policy` block was built to remove,
just hiding in a different family. It is also a live illustration of issue #36/#37's
CC→NS memory gap: even with the write routed correctly, there is no `results_history` row to
refine.

### Cases that cannot fail

- `pipeline.reject_non_directive`, **every** criterion is `pipeline_agent.*`, which
  `evaluate.py` auto-skips over HTTP. The case is unconditionally green and tests nothing.
- 18 turns carry auto-skipped criteria; 6 of those turns have **zero** remaining criteria
  after the skip (`pipeline.activation_rnaseq` t1, `pipeline.end_to_end_emit` t1,
  `pipeline.happy_path_nhp_rnaseq` t1, `pipeline.happy_path_scrnaseq` t0,
  `pipeline.sarek_multi_cohort` t1, `pipeline.tower_submit` t1, `pipeline.edit_after_sanity` t1/t2).
- 13 seed turns assert nothing at all.

### Redundancy

32 distinct question texts appear more than once, covering **94 turns**. The worst:
`find me mice treated with ndma` **x20**, `find mice treated with ndma` **x6**,
`submit` **x6**, `find all nhp samples in the database` **x5**.

`refine_and_recall` is 95 turns (21% of the corpus) built from ~6 distinct seeds and a
rotating follow-up, and 89% of it cannot judge an answer.

---

## What needs to change

### Structural, one place each: covers 353 variants without hand editing

1. **Extend `route_policy.families` in `overlay.json`** to every family in the table above.
   339 variants gain a top-level route assertion in one reviewable block. Low risk: every
   default matches observed behaviour in the 07-29 run.
2. **Fix the engine-aware family floor.** Still open from the prior wave: the floor keys off
   *family*, not the engine that ran, so `advanced.find_me_patients_from_the_gbm` correctly
   routes `graph_query` but lives in `search_advanced` and gets a REST floor a graph turn can
   never satisfy. Will generate false failures at a wider sample.
3. **Add a value floor tier.** The observability floor is right; it is just not sufficient.
   Ground truth exists for at least the 16 verified cases above, put those numbers on the cases.
4. **Make the unobservable-criteria skip visible.** A case whose criteria are *all* skipped
   should report `no-assertions` rather than `passed`.
5. **`graph_result.count` is meaningless for aggregate cyphers.** `routing.gbm_study_count`
   reports `count=1` because the cypher is `RETURN count(DISTINCT s) AS total`, one row
   containing zero. The floor should read the aggregate value, not the row count.

### Delete or rewrite

- 9 broken conversations (give each a real seed, or drop).
- 5 write-seed / NS-follow-up variants.
- `pipeline.reject_non_directive` as written.
- A share of the 94 duplicate turns.

### Fix the question

- 22 GBM variants that are not already `no_floor` (the other 4 are fine as honest-zero).
- `refrec.refine_to_female`: "the Kamm project" → lab.
- `refrec.how_many_distinct_labs_are_rep`: drop the hardcoded 264.

### Families to add (zero coverage today, highest real demand)

| family | turns | why | ground truth already established |
|---|---|---|---|
| `harmonization` | 3 | most-run question in the entire history (11 + 9 + 7 runs) | 195 NDMA mice; genotype histogram RG 44, RGATG 39, `RaDR R/R; gpt g/g` 33, RGA 30, `RaDR+/+; GPT+/+` 21, … GA 1, **exactly** reproduced from MySQL, matching the 07-29 probe |
| `pipeline_output_reingest` | 1-2 | declared task family, zero coverage, has never run to completion (issue #37) | two verified run dirs on Luria |
| `cross_mode_memory` | 2 x2 | NS→CC works, CC→NS does not (issues #36/#38) | asymmetry root-caused to `cc_assistant.py:552` |
| `attribute_search` | 1-2 | `find TIS samples where Scientist is Owen Leddy` = 9 runs | **7 TIS**; 144 total across D.MSP 83, A.MSP 34, CEL 14, TIS 7, CHM 5, BAC 1 |
| `lab_harmonization` | 1-2 | `Lab` has 24 values for ~12 real labs; a second, unexercised harmonization shape | full value table above |

---

## Issue #33: ground truth settled mechanically

| interpretation | count | how |
|---|---:|---|
| NHP **animals** that have sequencing data | **139** | NHP with any D.SEQ descendant |
| **D.SEQ samples** derived from an NHP | **1,608** | D.SEQ with any NHP ancestor |
| every NHP sample (the broken `advanced_search` OR) | 408 | `sampletype: [D.SEQ, NHP]` ORs the two types |

The `parents_by_child_types` endpoint ANDs its child types and returns **parents**.
Independently confirmed: `[D.FLOW, D.SEQ]` → 56 and `[D.IMG, D.SEQ]` → 104, both reproduced
exactly from Neo4j.

So 408 is wrong under every reading. But **139 and 1,608 are both defensible readings of
"Find NHP sequencing data"**, and the corpus never says which is meant. This is a
FIX-QUESTION as much as a product bug, and the consistency group cannot settle it because
it only asserts that the two phrasings agree.

---

## Open questions that need the operator

These are the ones no amount of querying can answer. They are listed in the companion
review sheet with a notes box each.

1. **GBM**, repoint the 22 at a real study (which?), or keep them as honest-zero tests?
2. **"Find NHP sequencing data"**, 139 animals or 1,608 D.SEQ samples?
3. **"4 week"**, is the right answer 2 (`Cohort = '4 week'` exactly) or 239 (including
   the 237 `'4wk'`)? This decides whether `green.refine_recall` is a pass or a fail, and
   whether search should harmonize before matching.
4. **`/admin/samples/retrieve/`**, is 7 rows for 1 requested UID the intended semantics?
5. **`Lab`**, now that 5,715 rows are known to carry it, does the D.SEQ "no Lab field"
   answer still count as correct?
6. **`pipeline_nfcore`**, `route_capabilities` puts build+launch on NS and reingest on CC.
   Does "build me a samplesheet" (no launch) stay NS?
7. **`reporting`**, do GEO/SRA/PRIDE generation turns stay NS, or is artifact-producing
   export CC's job under the settled bulk-export policy?
8. **`refine_and_recall` volume**, 95 turns from ~6 seeds. How many are worth keeping?
9. **The 101 ad-hoc questions**, ADD / ADD-VARIANT / SKIP, weighted by run count.
