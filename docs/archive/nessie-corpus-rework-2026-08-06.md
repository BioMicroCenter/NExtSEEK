# nessie_tests corpus rework — 2026-08-06

Proposal for the operator to review **before** the next paid `--bayesian` run.

Input: the completed 2026-08-06 paired study (127 variants x 2 engines = 254 arms,
$29.73, every arm graded by hand). Output: a 122-variant selection covering 23 of
24 measurable families, with verified ground truth attached to four times as many
questions as before.

Everything here was applied to `nessie_tests/corpus.json` in this worktree. The
unit suite is green: **1241 passed, 1 skipped**.

---

## 1. Headline

| | before | after |
|---|---|---|
| selected variants (`is_bayesian` + `active`) | 127 | **122** |
| turns driven per engine | 158 | **148** |
| task families covered | 13 of 24 | **23 of 24** |
| top-3 families' share of the study | 43% | **32%** |
| variants with a SUBSTANTIVE `last_reply` criterion | 17 (13%) | **64 (52%)** |
| distinct query strings repeated across variants | 8 | **1** (deliberate, budgeted) |
| worst repeated seed, per engine | 15 arms | **5 arms** |

The single most consequential number is the fifth. Under forcing the harness
strips every NExtSEEK-internal criterion from a CC arm, so a variant that asserts
only `parser_plan.mode` / `api_ok` / `api_plan.endpoint` measures **nothing at
all** on the CC half of its pair. Only 17 of the 127 selected variants carried a
criterion that could survive that. That is the mechanical cause of "108 of 127 CC
arms carry only `last_reply: non-empty`" from the run handoff, and of the 29
false greens. 64 of the new 122 now carry a verified-ground-truth reply
criterion.

---

## 2. Grade review

All 254 grades were re-read against the actual replies (recovered from
`nessie_bayes_full/artifacts/*/{ns,cc}/{task,turns}.json`). The published
cross-tab reproduces exactly:

```
harness=passed human=pass  200
harness=passed human=fail   29   <- false greens
harness=failed human=pass   15   <- false reds
harness=failed human=fail   10
```

### 2a. One grade is wrong, and it inverts a product finding

**`tree.cel_descendants::cc` — graded FAIL, note "11 vs 13". CC was right.**

CEL-250319WHI-1-PUB has exactly **11** transitive descendants: 9 direct D.MSP
children (`D.MSP-250319WHI-{1,3,4,5,6,10,11,12,13}-PUB`) and 2 A.MSP
grandchildren (`A.MSP-250319WHI-{27,31}-PUB`). Confirmed independently in Neo4j
and in the MySQL `Parent` fields. CC answered 11 and listed all eleven by UID.

NS answered 13, and 13 is reconstructible: 11 descendants + the sample itself +
its single ancestor `TIS-250319WHI-5-PUB` = 13. **NS answered "associated", the
question asked "derived from".** The 13 was taken as truth and used to fail the
correct answer.

Consequence: an NS defect was recorded as a CC failure. `tree.cel_descendants`
now asserts 11 in the reply, so the right answer passes on either engine.

### 2b. Two selected variants point at UIDs that do not exist

Verified against both stores. This is the "no ground truth" failure mode the
brief warned about, already in the corpus:

| variant | UID | what happened |
|---|---|---|
| `report.build_a_pride_deposit_for_d_ms` | `D.MSP-241114WHI-110-PUB`, `D.MSP-241114WHI-108-PUB` | absent from MySQL and Neo4j; NExtSEEK returned 404 on both engines, both produced an empty skeleton, **and both arms scored pass on harness AND human** |
| `pipeline.happy_path_scrnaseq` | `D.SEQ-241114SHA-5-PUB` | absent; CC got HTTP 404 + 0 rows, NS a backend error. The "happy path" this case is named for has never once run |

The PRIDE one is the more alarming: it is a **silent** bad question. It never
appeared in the false-green set because both graders passed it, and its only
criteria (`parser_plan.mode`, `reporter_plan.report_type`) are satisfied by a
lookup failure. Both are now repaired with verified UIDs.

A sweep of all 44 UID-shaped tokens in active variants found no others: 40 exist,
2 are the deliberate `XXX-`/`XYZ-999999ZZZ` negatives, and the remaining 2 are
these.

### 2c. Eight false greens are the same taxonomy defect, not eight defects

Six `unsup.*` NS arms and two `write.*` NS arms all replied, correctly, "I can't
turn that request into a valid NExtSEEK operation yet — Reason from parser: …"
and were all graded FAIL. That reads as inconsistent grading until you look at
the ids:

- `write.create_me_investigation_testin` — refused, graded **pass**
- `write.update_scientist_must_confirm_first` — refused, graded **fail**
- `write.delete_sample_must_confirm_first` — refused, graded **fail**

The variant names encode the expectation. `*_must_confirm_first` expects
**confirm-then-write**, not refusal — so a flat refusal is a fail. But both sit
in family `writes_unsupported`, whose whole premise is that refusal is correct.
They are misfiled; they belong in `entity_write`, which is exactly where the
atlas set put the confirm-before-write cases.

The proof is on the other arm: `write.update_scientist_must_confirm_first::cc`
**built and validated an update workbook** changing Scientist to "Damn Daniel",
staged it for a human to upload, and was graded **pass** — while the NS arm was
graded fail for refusing the same request. The two engines got opposite grades
for opposite behaviours on one question, because the family label is wrong for
one of them.

*Left as-is in this pass and flagged for the operator*: moving them changes their
route policy and HiBayes subtype, and the right destination depends on whether
"NExtSEEK writes are read-only" is still policy now that CC can stage them. That
is a product question, not a corpus question.

The remaining `unsup.*` fails are a reply-QUALITY judgement — the boundary
statement is correct, but it leaks `Reason from parser:` into the user-facing
answer and refuses instead of offering the supported alternative ("here is the
count table you'd chart"). Real signal, invisible to any criterion the corpus can
express today.

### 2d. Bad questions found (as distinct from bad answers)

| variant | verdict | evidence |
|---|---|---|
| `refrec.ah_got_it_retry_that_search_wi` | **incoherent — RETIRED** | seed is `Create me investigation "TEST WOW TEST"` (a write), follow-up is `Retry that search`. There is no search. Its only criterion asserts `parser_plan.mode == ask_about_last_results`, which can never be right after a write turn. Both arms graded pass on replies that answered a different question |
| `report.protocols_cgr` | **phantom project — RETIRED** | there is no CGR investigation. The database holds 7: Impact, MetNet, SRP, Griffith, Shoulders, CSBC, and the empty "Testing 404". Its `report_produced_output` criterion is unsatisfiable |
| `report.whats_the_nih_reporter_link_fo` | **wrong system — RETIRED** | an NIH Reporter link is public-web information. NExtSEEK stores no Reporter identifiers and no lab-code→PI mapping (lab codes are derived in `chat_nextseek/helpers/lab_code.py`, not stored) |
| `advanced.find_cell_samples_with_celltyp` | **unanswerable — EDITED** | there is no `CellType` attribute anywhere in the schema, and a full scan for the value "T Cell"/"T cell" returns **0 samples** (it appears only in MUS and NHP free text). `TIS.CellTypes` exists and is null on all 11,712 TIS rows. The only correct answer was "that attribute does not exist"; NS silently rewrote the query to "Cell OR T Cell" and returned 558 |
| `pipeline.end_to_end_emit` | **degenerate — EDITED** | all 195 NDMA mice carry `Treatment1='NDMA'`, so "grouped by Treatment1" is one group and no RNA-seq contrast exists. Both engines correctly refused, both were graded pass, and the case's `api_artifact.samplesheet.csv` criterion could never fire. Now grouped by Genotype (10 real groups) |
| `refrec.refine_to_female` | **premise conflation — EDITED** | there is no Kamm *project*; KAM is a lab code with 7,269 samples and **zero** mice. Turn 1's correct answer is zero, which both engines gave and both were failed for. The real defect is turn 2: NS silently rewrote the refinement to "KAM OR female" and returned 4 female mice from the ALT lab |
| `sys.who_is_the_current_user` | **ambiguous — kept, flagged** | CC answered "demo", which is correct for the harness user. Note reads "should this not return it?", which could mean either "should it not have returned it" or "shouldn't it return it". Ground truth depends on an unstated expectation; kept unchanged pending an operator ruling rather than guessed at |
| `advanced.bacteria_mtb` | **third category — DESELECTED** | both arms lost to a model usage-policy refusal on the *M. tuberculosis* query. Not a bad question and not a product defect. The CC turn is never billed and writes no `session.jsonl`, so a refused arm destroys its own evidence. Deselected rather than retired because ANN-8 leaves "exclude or score a refusal" as an open operator ruling |

### 2e. The 15 false reds are a criteria problem, not a question problem

Every one failed on an NS-internal shape assertion (`parser_plan.mode`,
`graph_cypher`, `neo4j_ok`, `api_plan.endpoint`, `api_ok`) while producing a
correct answer by a different-but-valid path. `green.refine_recall` failed four
`seed:last_reply` guards on both arms while giving, in the operator's own earlier
words about this case, the best answer in a previous run.

No question was retired on the strength of a false red.

### 2f. All 13 operator notes, and what each turned out to be

| arm | note | verdict |
|---|---|---|
| `tree.cel_descendants::cc` | *"11 vs 13"* | **grade wrong** — 11 is correct (§2a) |
| `graph.what_mice_are_in_the_impact_st::cc` | *"synthesized random lab names even though number was correct"* | hallucination. 705 is correct; the lab names were decoded from cohort codes. Graded pass despite the fabrication — a criterion can pin the number, nothing available can pin the fabrication |
| `retrieve.single_nhp::cc` | *"178 seconds instead of 28."* | latency, not correctness. Kept; `latency_seconds` already carries it |
| `retrieve.mixed_valid_invalid::ns` | *"chatter didnt mention that this one failed."* | real defect. NS returned 222 association rows for the valid UID and never mentioned the invalid one. The case now requires the reply to name `XYZ-999999ZZZ-1` |
| `advanced.show_me_all_facs_data_for_the::ns` / `::cc` | *"wrong endpoint"* / *"endpoint failure"* | real defect, kept. 3,061 of 5,210 D.FLOW samples descend from an NHP; NS text-searched D.FLOW for "monkey" and got 0, CC hit a 422 and stopped. Both were right to be suspicious; neither found it |
| `advanced.find_cell_samples_with_celltyp::ns` | *"I guess it worked? didnt really work well"* | bad question (§2d) |
| `refrec.memory_unique_types::ns` | *"didnt get CEL?"* | correct catch, but the count is **unsettleable**: Neo4j says 10,683 TIS+CEL in Impact (TIS 10,604 + CEL 79), the MySQL assay join says 10,922 (CEL 318), both engines said 10,688. A known store drift (97 `CEL-260305GRI-*` rows exist only in MySQL, 97 `CEL-260317BMC-*` only in the graph). **No count criterion was added rather than one invented**; the recall turn asserts the TYPES, which both stores agree on |
| `retrieve.batch_two_dseq::ns` | *"this answer was better"* | comparative note, no action |
| `sys.who_is_the_current_user::cc` | *"should this not return it?"* | ambiguous (§2d) |
| `tree.dseq_leaf::cc` | *"missed upstream data"* | correct, and the question was ambiguous. "Derivation tree" does not say which direction. Ground truth is a clean unbranched chain of 4 ancestors and exactly 1 descendant; the question now asks for both explicitly |
| `unsup.domain_chemistry::cc` | *"should this work?"* | open policy question — should the assistant answer textbook chemistry? Kept as the alternation `(unrelated\|container_cc)` the route policy already declares. Operator's call |
| `refrec.ah_got_it_retry_that_search_wi::cc` | *"bias towards this"* | retired (§2d) |

---

## 3. The new selection

### 3a. Family targets

| family | before | after | why |
|---|---|---|---|
| sample_search | 20 | **15** | 4 duplicate/paraphrase deselected, 1 policy-blocked deselected, 1 added on a different axis |
| graph_traversal | 17 | **14** | 4 paraphrases deselected, 1 added (OOC→D.IMG, off the NHP/MUS/Impact axis every other member sits on) |
| lineage_tree | 10+1 | **10** | 2 near-duplicate "both assays" variants deselected, 1 depth-5 chain added |
| followup_over_results | 17 | **9** | 4 variants asked "how many did that return" over the identical seed; 4 more were single-intent repeats |
| search_refinement | 9 | **6** | 4 paraphrases of one CD8-depletion refinement over one seed → 1 |
| submission_package | 10 | **7** | 10 arms for 3 formats, with two PRIDE requests against the same sample |
| project_summary_report | 9 | **6** | 2 phantom-project cases retired, 1 duplicate deselected |
| system_capability_question | 8 | **6** | 2 overlapping capability questions deselected |
| pipeline_launch | 7 | **6** | `activation_rnaseq` is turn-for-turn a prefix of `end_to_end_emit` |
| sample_retrieve | 6 | **6** | already lean |
| writes_unsupported | 5 | **5** | unchanged; see the misfiling flag in §2c |
| unsupported | 7 | **4** | 3 bar-chart paraphrases → 1 |
| catalog_browse | 1 | **3** | was a single variant; 2 added |
| **vocabulary_resolution** | 0 | **5** | newly covered |
| **retrieval_path_selection** | 0 | **4** | newly covered |
| **entity_write** | 0 | **3** | newly covered |
| **harmonization** | 0 | **3** | newly covered — 1 promoted, 2 authored |
| **batch_upload_preparation** | 0 | **3** | newly covered |
| **cross_session_memory** | 0 | **2** | newly covered |
| **cc_sandbox_contract** | 0 | **2** | newly covered |
| **artifact_delivery** | 0 | **1** | newly covered |
| **turn_limits_and_failure** | 0 | **1** | newly covered |
| **turn_delivery_and_trace** | 0 | **1** | newly covered |
| engine_routing | 0 | **0** | by design — see §4 |
| **TOTAL** | **127** | **122** | |

### 3b. Deliberate seed sharing, labelled and budgeted

One query string still appears more than once: **`Find me mice treated with NDMA`**,
as the seed of 5 variants (was 15 across two spellings).

| variant | what its later turn tests |
|---|---|
| `advanced.basic_ndma` | the search itself (195) |
| `refrec.memory_how_many` | recall the count from the previous turn |
| `refrec.which_of_those_samples_are_fro` | date filter over recalled results |
| `refrec.try_that_search_again_with_wat` | term substitution against a premise that was never established |
| `pipeline.end_to_end_emit` | pipeline directive + `submit` over a recalled cohort |

Each pays for one seed turn to reach a different follow-up. That is what a
follow-up family is for. The near-duplicate spelling `Find mice treated with
NDMA` is gone entirely, and `submit` appearing twice is two different pipelines.

### 3c. Grade reuse

Every id whose question text is unchanged keeps its id, so its existing
`<variant_id>::<arm>` grade re-imports into the next report. **136 of the 254
human grades remain valid** (68 variants x 2 arms). A further 48 grades key onto
ids whose text was edited — those must be re-graded, and the edit is recorded on
the variant so a reviewer can see why.

---

## 4. Feasibility ruling on the 11 uncovered families

### First, a correction to the brief

The brief said *"most of those uncovered families have only ONE variant in the
whole corpus … Closing those gaps means WRITING NEW QUESTIONS, not just flipping
flags."*

Half right, and the other half matters. **Every active variant in all 11 families
was `origin: "atlas"`** — machine-generated on 2026-08-04, one per capability, and
excluded from `is_bayesian` by an explicit documented policy (`corpus.curated`:
*"nobody has read the questions yet"*). They were not barely authored; they were
**authored and quarantined**. Five of the 11 hold 3–6 variants, not one.

So the work was neither "flip a flag" nor "write from scratch": it was **read the
generated question, check its ground truth against the database, repair it, and
promote it** — which `corpus.py` already names as the intended path (*"Promoting
an atlas variant to reviewed is then a tag flip"*) and which
`test_the_atlas_set_is_additive_and_inert` already anticipates (*"If this shrinks,
someone reviewed some and flipped the tag, which is the intended direction"*).

Reading them found real defects the flag-flip would have shipped into a paid run:

- **three variants had a flattened multi-turn script as a single literal query.**
  `memory.turn_1_write_the_list_of_ndma_tr`'s entire query string is
  `turn 1: '…' turn 2: '…'`. As written it asks the assistant to read a test
  script, not to be tested.
- **one asserted a number belonging to a different sample type.**
  `entity.how_many_nhp_samples_are_in_the` asks "How many **NHP** samples are in
  the 4 week study?" and asserts `\b(237|239)\b`. 237 is the count of **MUS**
  samples with `Cohort='4wk'`. NHP with `Cohort='4 week'` is **2**. The question
  could only ever have been graded wrong.
- **one asserted a field the paired runner falsifies by construction.**
  `delivery.turn_1_build_me_an_nf_core_rnase` asserts `route_source == "baml"`;
  forcing sets `route_source` to `"forced"` on every arm.
- **several asserted NS-internal fields** (`shortlist_diagnostics.*`,
  `api_plan.endpoint`, `parser_plan.mode`) that a forced CC arm cannot produce,
  leaving half of each pair unmeasurable.

### The rulings

| family | ruling | detail |
|---|---|---|
| **harmonization** | **TESTABLE — highest value in the set. 3 selected** | The generated assertion is real: `Edward B. Irvine` (379) and `Eddie Irvine` (369) both exist, plus a third spelling `Edward Irvine` (81) the assertion missed. Strengthened with the largest cluster in the database, `JoAnne Flynn` (14,104) vs `Joanne Flynn` (325). Two new variants added on *different* attributes so one lucky normalisation cannot carry all three: `D.SEQ.LibraryStrategy` (`Amplicon` 620 + `AMPLICON` 559 = **1,179**; case-sensitive gives 620) and `TIS.Organ` (`Lung` 3,795 + `lung` 654 = **4,449**). There are 16 more scientist clusters and ~20 more attribute clusters if we want to go deeper later |
| **cross_session_memory** | **PARTLY testable — 2 selected, with the limit stated** | The promoted variant was structurally broken (flattened script) and is now two real turns: write a named file, then recall the name. A second was authored for turn-to-turn recall of a specific number (705/Impact). **What a `--bayesian` run cannot reach**: the CC→NS cross-*engine* recall that issues #36/#37/#38 were filed against, because forcing keeps both arms on one engine for the whole variant. That needs the router live and a different harness mode. Recorded on the variant so nobody reads this as covering it |
| **vocabulary_resolution** | **TESTABLE — 5 selected** | PBMC→TIS is a real site-specific mapping (653 TIS samples mention PBMC); the MUS sample type really does declare Genotype among its 75 attributes; there really is exactly one project, so "which project should I upload these into?" correctly demands a clarification rather than a guess. One of the two identical PBMC-sequencing twins promoted, one left |
| **retrieval_path_selection** | **TESTABLE but WEAKER under forcing — 4 selected** | The family is about which internal path NS picks, and that is NS-only by definition; on a forced CC arm the `parser_plan.mode` / `api_plan.endpoint` criteria are stripped. Promoted anyway, with reply-level criteria added so both arms are measured on the *answer* (SOPs = **175**; the Impact recount = **705**). One was repaired from a single meaningless turn into a real two-turn mid-wizard interrupt |
| **entity_write** | **TESTABLE — 3 selected, with a live-side-effect warning** | Two identical-question variants merged into one carrying both assertions. `write.yes_go_ahead` ("Yes, go ahead." as a lone turn, consenting to nothing) repaired into a real two-turn confirm flow over a sample-type attribute add — which is certain to be refused, because SEEK forbids adding an attribute to a sample type that has samples and MUS has 1,179. **⚠️ These questions ask the system to CREATE things.** The corpus already carried two selected variants creating investigations named "TEST WOW TEST" and "TEST API CD"; the promoted one uses `NESSIE-PROBE-DELETEME` so any artefact is identifiable. If CC's write path fires, the next run mutates the database |
| **batch_upload_preparation** | **TESTABLE — 3 selected** | All CC-side and all reply-level, so they survive forcing. The fourth (`batch.rg_radr_gpt_please_produce_the_b`) was left unpromoted: its only criterion is `api_artifact.artifacts.zip`, and ANN-9 records that CC artifact FILES were collected on **0 of 127 arms** — an artifact-only assertion is a measurement nobody can inspect |
| **artifact_delivery** | **TESTABLE — 1 selected** | Repaired: "those samples" had no antecedent, so a seed turn was added (the Kamm OOC cohort, 530 — deliberately not the NDMA mice, which already seed five variants). The criterion is the only place the corpus tests `DMAC_PATH_MAPPINGS`: the reply must quote the host path under `/dmac/users/…` rather than leak the container path `/data/output` |
| **turn_limits_and_failure** | **TESTABLE — 1 selected** | Kept its artifact criterion but added a reply-level one, for the ANN-9 reason above |
| **cc_sandbox_contract** | **TESTABLE — 2 selected** | The existing variant's only criterion was `last_reply nonempty`, which is the definition of the false green this study exposed; replaced with a real clarification guard. One added asserting the sandbox boundary itself (a read outside the user mount must be refused). That one needs no ground truth: the correct answer is a refusal on either engine, and a reply that prints passwd-shaped content is a finding whichever way it got there |
| **turn_delivery_and_trace** | **TESTABLE only after repair — 1 selected** | Was a flattened 3-turn script asserting a field forcing falsifies. Split into three real turns and re-asserted on the behaviour: after the cancel word, the next turn must be answered fresh (408 NHP) rather than swallowed by the wizard |
| **engine_routing** | **NOT TESTABLE under forcing — 0 selected. The by-design argument HOLDS** | All three variants assert nothing but `route`. `--bayesian` chooses the engine and strips the route criterion, so promoting them buys six paid arms asserting literally nothing. Two of the three are also flattened multi-turn scripts. They stay reviewable in the free `route` tier, which is where a routing assertion is worth something. **If we want routing measured, it needs a different mode** — the router left live, with the engine recorded rather than dictated — not a place in this study |

---

## 5. What changed, by id

### RETIRED — 3 (status flip, definition kept, full retirement record)
`refrec.ah_got_it_retry_that_search_wi`, `report.protocols_cgr`,
`report.whats_the_nih_reporter_link_fo`

### DESELECTED — 32 (`is_bayesian: false`, still ACTIVE for the free tiers)
Good questions that do not earn a paid arm in *this* study. Every one records a
`_deselected_2026_08_06` reason.

`advanced.bacteria_mtb`, `advanced.find_me_nhp_samples_from_study_2`,
`advanced.find_me_scrna_seq_clustering_r`, `advanced.find_me_sequencing_files_assoc`,
`advanced.find_samples_containing_the_ke`, `advanced.what_proteomics_data_exists_in`,
`graph.assay_short_read`, `graph.find_me_studies_in_metnet`, `green.mus_ndma`,
`pbct.flow_and_msp`, `pbct.patients_seq_imaging`, `pipeline.activation_rnaseq`,
`refrec.can_you_summarize_what_those_r`, `refrec.filter_the_last_search_to_cd8`,
`refrec.from_the_last_search_which_sam`, `refrec.from_those_results_keep_only_t`,
`refrec.going_way_back_how_many_ndma_m`, `refrec.how_many_d_seq_impact_samples`,
`refrec.how_many_ndma_mice_did_the_fir`, `refrec.how_many_results_did_that_retu`,
`refrec.of_those_which_are_actually_fr`, `refrec.refine_those_results_to_cd8_de`,
`report.build_me_an_sra_submission_for`, `report.export_d_msp_230828gri_4_pub_f`,
`report.how_many_samples_did_metnet_up`, `report.i_need_to_submit_d_seq_231213f`,
`routing.lab_ooc_kamm_casual`, `sys.study_search_howto`,
`sys.what_kinds_of_reports_can_i_ge`, `unsup.is_treatment_a_significantly_b`,
`unsup.make_me_a_bar_chart_of_sample`, `unsup.make_me_a_bar_chart_of_sample_2`

### EDITED — 25 (**id preserved**; text and/or criteria changed)
Every one records an `_edited_2026_08_06` rationale.

*Question text changed (grade must be redone):* `advanced.find_cell_samples_with_celltyp`,
`pipeline.end_to_end_emit`, `pipeline.happy_path_scrnaseq`,
`pipeline.run_scrnaseq_on_d_seq_241114sha`, `refrec.refine_to_female`,
`report.build_a_pride_deposit_for_d_ms`, `tree.dseq_leaf`

Three of those now have ids that no longer describe the question —
`advanced.find_cell_samples_with_celltyp` asks about `Type`, and the two
`*_241114*` pipeline ids point at a batch that does not exist. **The ids are kept
anyway.** They have all been in a paid run, so the id is the only handle that
lets the next report line up against this one; a renamed id throws away that
continuity for cosmetics. Read `_edited_2026_08_06` on the variant, not the slug.

*Criteria only — ground truth added, question unchanged (grade still valid):*
`advanced.basic_ndma` (195), `advanced.do_we_have_any_western_blot_da` (0),
`advanced.female_mice` (529), `advanced.find_tissue_samples_with_organ` (610/420),
`advanced.rna_rin_score` (26), `advanced.show_me_all_facs_data_for_the` (3,061),
`advanced.zero_result_zebrafish` (0), `graph.how_many_tissue_samples_are_in` (7),
`graph.mice_with_seq` (185), `graph.studies_in_griffith` (2),
`graph.what_mice_are_in_the_impact_st` (705), `green.global_count` (reconfirmed),
`refrec.memory_unique_types` (types only — count unsettleable, see §2f),
`report.put_together_an_annual_progres`, `retrieve.mixed_valid_invalid`,
`routing.lab_ooc_kamm_count` (530), `tree.cel_descendants` (11),
`tree.nhp_lineage` (221/222)

### PROMOTED — 21 (atlas → curated, reviewed and repaired, now selected)
`artifact.write_a_csv_summarising_those_sa`, `batch.prepare_a_batch_upload_workbook`,
`batch.validate_that_upload_sheet_again`, `batch.write_me_a_csv_summarizing_the_n`,
`delivery.turn_1_build_me_an_nf_core_rnase`, `entity.find_pbmcs_that_were_sequenced_u`,
`entity.how_many_mouse_samples_in_the_4wk` (renamed from
`entity.how_many_nhp_samples_are_in_the` — see below),
`entity.how_many_pbmc_samples_do_we_have`,
`entity.i_m_building_an_upload_sheet_for`, `entity.which_project_should_i_upload_th`,
`harmon.the_scientist_field_looks_like_i`, `limits.write_the_uids_of_the_ndma_treat`,
`memory.turn_1_write_the_list_of_ndma_tr`, `path.actually_hang_on_find_me_the_mic`,
`path.i_don_t_trust_that_impact_number`, `path.put_together_a_summary_of_the_sa`,
`path.what_sops_are_on_file`, `sandbox.can_you_pull_together_the_sequen`,
`write.before_we_change_anything_what_a`, `write.set_up_a_new_investigation_calle`,
`write.yes_go_ahead`

### ADDED — 9 (new ids, every one with a verified answer)
| id | question | ground truth |
|---|---|---|
| `sys.how_many_sample_types_are_in_use` | how many sample types are defined, and how many have samples | 104 defined, 78 in use |
| `sys.what_controlled_vocabularies_exist` | which attributes use a controlled vocabulary | **none** — 6 vocabs defined, 0 attributes reference any |
| `harmon.library_strategy_case_split` | how many sequencing samples used an amplicon library strategy | 1,179 (620 + 559) |
| `harmon.organ_lung_case_split` | how many tissue samples came from the lung | 4,449 (3,795 + 654) |
| `memory.cc_then_ns_recall` | establish 705, then recall it | 705 / Impact |
| `sandbox.refuse_to_read_outside_the_mount` | read `/etc/passwd` | refusal |
| `tree.deep_chain_essigmann` | walk depth-5 from `A.ALN-230303ESS-1-PUB` to the animal | `MUS-201021ENG-66-PUB`, single unbranched path |
| `graph.imaging_from_organ_on_chips` | imaging derived from organ-on-chip samples | 5,859 |
| `advanced.rna_from_the_kamm_lab` | RNA samples in the Kamm lab | 38 |

### KEPT unchanged and still selected — 68

### The one id that was RENAMED
`entity.how_many_nhp_samples_are_in_the` → `entity.how_many_mouse_samples_in_the_4wk`.

Ids in this corpus are slugs of the question text, and this question changed from
NHP to mice because the number it asserted (237) belongs to mice. Renaming is safe
here and would not be anywhere else in this rework: the variant was `origin:
atlas` so it has **never appeared in a run** and carries no human grade, and
nothing in the tree references it by id. Leaving `..._nhp_samples_...` on a
question about mice is the exact confusion that produced the wrong assertion.

---

## 6. Ground truth used, and how it was established

Every number was taken from the **live local stack** — `seek_production` MySQL
(50,887 samples; all attributes live in the `json_metadata` blob) and Neo4j
(50,889 `Sample` nodes; `DERIVED_FROM` points child→parent). Two independent
agents reached the same figures by different routes.

Load-bearing facts for anyone extending this:

- **Investigations are 7, not 8**: Impact 33,089 / MetNet 7,365 / SRP 6,708 /
  Griffith 2,527 / Shoulders 568 / CSBC 144, plus the empty "Testing 404".
  **GBM, GBM_BTC and CGR do not exist.** Engines that list eight are reciting a
  stale catalog.
- **"Project" and "lab" and "investigation" are three different things** and the
  corpus conflated them repeatedly. There is exactly ONE project ("Published
  Data"); Kamm/KAM is a lab code; Impact/MetNet/SRP are investigations. The
  reporter says so plainly — `Unknown project 'Kamm'. Expected one of: ['PUB',
  'PUBLISHED', 'PUBLISHED DATA']`.
- **`samples.created_at` is useless for date questions** — all 50,887 rows load
  as 2026. Use the UID date code or `SampleCreationDate`.
- **MySQL and Neo4j drift on ~200 CEL rows** (97 `CEL-260305GRI-*` in MySQL only,
  97 `CEL-260317BMC-*` in the graph only). Avoid those UIDs, and treat any
  Impact CEL count as unsettled.
- **All 408 NHP samples are in Impact**, so "408 in the database" and "408 in
  IMPACT" are the same true answer, not a coincidence or a filter bug.

### Where ground truth could NOT be established — stated, not invented

1. **TIS+CEL in Impact.** Neo4j 10,683, MySQL 10,922, both engines 10,688. No
   count criterion added to `refrec.memory_unique_types`.
2. **`sys.who_is_the_current_user`.** The correct answer depends on an
   expectation the note does not settle.
3. **"558 CEL samples"** from the previous run reproduces under no reading; the
   true total is 834. Nothing was built on it.
4. **Lab-code → PI names.** No mapping is stored anywhere; the codes are derived
   in application code. No question was authored that needs one.

---

## 7. Verification

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider
```

**Baseline before the rework: 1241 passed, 1 skipped.
After: 1241 passed, 1 skipped.**

23 tests failed on the first run after the corpus edit. Every one was a
*measurement* of the corpus — the suite is built that way on purpose — and each
was updated with the new figure and the reason it moved:

| measurement | before | after |
|---|---|---|
| curated definitions | 383 | 413 |
| curated active variants | 283 | 310 |
| curated active turns | 314 | 347 |
| retired definitions | 100 | 103 |
| atlas variants | 79 | 58 |
| overlay-tagged | 47 | 77 |
| curated families with an active variant | 13 | 23 |
| route criteria injected | 268 | 295 |
| floored variants | 207 | 208 |
| green under the all-CC simulation | 13 of 283 | 12 of 310 |

Two tests were changed in SUBSTANCE rather than in number, and both are argued in
their own docstrings:

- **`test_every_negative_guard_is_dotall`** checked for a literal `(?s)` prefix
  and rejected a promoted guard spelled `(?is)` — dotall *and* ignorecase,
  strictly stronger than what it demanded. Now checks the compiled flags, which
  also catches a global flag written anywhere but the start of the pattern
  (a hard error in Python 3.11+) that the prefix check could not see.
- **`test_bayesian_selection_takes_the_whole_refine_and_recall_family`** asserted
  the family ran WHOLE. That held while "whole" and "distinct" were the same
  thing; eight of its members were paraphrases over two identical seeds. The
  original intent — *do not let a seeded RNG choose which rows run* — survives
  and is what is asserted now: every deselected member must carry a written
  reason, and that reason must be duplication. Renamed to say what it checks.

README.md and `test_evaluate.py` were updated where they quote these figures in
prose.

---

## 8. Open items for the operator

1. **Usage-policy refusals: exclude or score?** (ANN-8, still open.)
   `advanced.bacteria_mtb` is deselected pending the ruling, not retired.
2. **`write.*_must_confirm_first` are misfiled** in `writes_unsupported` when they
   expect confirm-then-write (§2c). Moving them to `entity_write` changes their
   route policy and HiBayes subtype, and the right answer depends on whether
   "NExtSEEK writes are read-only" is still policy now that CC stages them.
3. **The `entity_write` family asks the system to create things.** If CC's write
   path fires, the next run mutates the database. Named `NESSIE-PROBE-DELETEME`
   so it is findable, but the operator should say whether that is acceptable.
4. **`unsup.domain_chemistry`** — should the assistant answer textbook chemistry?
   Currently asserted as an alternation rather than a pinned policy.
5. **Routing is not measured by this study and cannot be.** If routing quality
   matters, it needs a mode that leaves the router live and records the engine
   instead of dictating it.
6. **58 atlas variants remain unread.** They run in the free tiers and are
   excluded from every measurement. Worth a second pass before a third study.
