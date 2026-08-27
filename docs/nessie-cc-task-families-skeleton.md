# Container-CC task families: authoring skeleton

**Status:** FILLED and runnable. 2026-07-31. Every family below is seeded from questions you
actually asked; the first pass is authored as
`nessie_tests/probes/probe-cc-2026-07-31.json` (13 cases, 16 turns, ~$3).

**Three real questions were deliberately NOT used** because they are `example_queries` inside
`route_capabilities.json`, so testing them measures literal recall rather than routing:

| burned question | why |
|---|---|
| "Which samples belong to the CD8 depletion study? I don't think it's a project." | exact match, `ambiguous_study_resolution` example[0] |
| "Cluster these samples by their metadata and tell me what the groups have in common." | exact match, `open_ended_analysis` example[2] |
| "write a csv summarizing those samples, uid, sample type, project, etc" | 0.81 similar to `export_and_file_delivery` example[1] — **my fault**, I derived that example from your question when writing the candidate. Change the example and you get the question back. |

Four more sit at 0.60-0.69 similarity and were paraphrased rather than dropped.

**Why this exists.** The corpus has 280 active variants; 267 route `nextseek_query` and 8 route
`container_cc`. Four of the seven CC families have zero coverage and no CC route-gate case
survives. Nothing in the prior CC work fills this: those harnesses **force** the route
(`proof_paradigm: forced_cc_direct`, and `expected_route` is `null` on all ten catalog
exercises), so they prove an op works when reached and say nothing about whether a question
reaches it.

**Seeded from real usage.** 38 of the 101 questions recovered from your own chat history route
CC. They appear below under their family with run counts. Those have demonstrated demand, so
prefer them over invented ones.

---

## Before you write anything: what can actually be asserted

A CC turn exposes **four fields**. Everything else is null, because a CC `query_complete`
carries no `debug` payload at all.

| field | notes |
|---|---|
| `route` | `container_cc` |
| `route_source` | `baml` / `heuristic` / `forced` / `pipeline`. **Always pin this to `baml`.** |
| `engine` | the constant `container_cc:opus`, so it proves nothing |
| `last_reply` | prose. `nonempty`, `mentions <token>`, `matches_re <pattern>` |

Three rules that follow:

1. **Always assert `route_source eq baml` next to `route`.** The heuristic fallback at
   `nextseek_api/cc_assistant/router.py:50-54` matches `write|script|code|plot|read|file|summarize`
   — exactly this vocabulary. Without pinning the source, a CC case passes even when BAML is down.
2. **Never assert `api_artifact.*`.** `evaluate.py:60-76` reads `query_complete["files"]`;
   CC emits `query_complete["artifacts"]` and `["cc_raw_files"]`. Different keys, so it always
   resolves false. Fixing that one function is the highest-value harness change available.
3. **Never put a CC case in an NS-floored family.** `search_advanced`, `search_retrieve`,
   `search_parents_by_child`, `search_tree`, `graph_query` and `reporting` attach floors
   (`api_outcome_observed`, `graph_outcome_observed`, `report_produced_output`) that are constant
   false on a CC turn, so the case is an automatic red.

Assertion ideas worth stealing from `nextseek_api/assistant/tests/test_granular_realstack.py`,
once artifacts are observable: `:178` real rows returned, `:200`/`:211` the write blocked-vs-confirmed
legs, `:226` report carries DB values, `:265` submission built. Today none of these are reachable
from a CC turn — they are the argument for fixing the artifact keys.

---

## The families

Names are ours to set. Where I propose a rename from `route_capabilities.json`, the reason is
that the shipped description does not match what the code does.

**Settled 2026-07-31: eight family strings, not one.** The alternative was one `nessie_cc`
family carrying the eight intents as tags, which would have been cheaper. Accepted cost:
`corpus.sample` keeps `max(1, round(len * ratio))` **per family** (`nessie_tests/corpus.py:330-332`),
so eight families means **eight guaranteed container_cc turns in every `--sample 0.1` run** —
about +$1.60 and +10-25 min, permanently. Mitigation: build the route gates as in-container
checks calling `cc_router.decide()` directly (the pattern at
`nextseek_api/cc_assistant/tests/test_cc_realstack.py:155-175`), which costs one BAML call
instead of a full Opus turn.

**A family is a feature, not an op.** Settled 2026-07-30. A task family exists because it is
something a user asks for and we want consistently tested. Some families compose several ops
(`batch_upload_preparation` runs a four-op chain), some compose ops plus agent judgement
(`harmonization`), and some use **no plugin op at all** — `code_and_scripts`,
`open_ended_analysis`, `export_and_file_delivery` and `ambiguous_study_resolution` run on the
stock Claude Code toolset. That is not a coverage gap; it is what those features are.

The corollary matters when reading any op-coverage table: **op coverage and family coverage are
different measurements.** Of the 20 `nextseek-*` ops, the eight families below touch 11. The other
nine (`query`, `recall`, `parse`, `plan`, `graph`, `report`, `api-read`, `entity-extract`,
`generate-submission`) are the ops CC uses to call *back into* the NExtSEEK pipeline, and they
belong to the NS-side families by intent. Do not treat their absence here as a hole to fill.

---

### 1. `batch_upload_preparation`
> Build and validate a reviewable NExtSEEK upload workbook from user-supplied material.
> Never uploads.

**Routing tell:** the user supplies material to turn into sample rows, or asks to change values
on existing samples. Contrast with `sample_search`, which only wants records back.

**Verified working:** the shim chain `project-resolve → sampletype-attrs → build-payload →
validate-upload` is real and was proven end to end in a paid run. Harmonization works: a 2026-07-29
probe produced a genuine 188-row merge mapping and correctly refused to write.

**Blocked / constrained:**
- `nextseek-validate-upload` needs `--rows`, `--project-id` **and** `--project-confirmation`
  together. The rows file comes from a user upload endpoint the harness cannot call, so a
  single-turn case cannot reach the terminal op.
- Do not hand CC a PDF or DOCX. `markitdown` ships without format extras, so extraction exits 2.

**Reachable in one turn:** `project-resolve` against the live project list, `sampletype-attrs`
for a named type.

**Your real questions (11 distinct, 22 runs):**

| runs | question |
|---:|---|
| 2 | Please continue and finish building and validating the workbook; report the validated workbook path and verdict. |
| 1 | Can you prepare an update sheet to update these samples? |
| 1 | Confirmed project is "Published Data". Please produce batch upload workbook. |

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |

---

### 2. `harmonization` — a composed feature, not an op
> Find near-duplicate values of an attribute across the catalog, propose a canonical merge
> mapping, and hand it to `batch_upload_preparation` to become a workbook.

**This is your single most-run question in the entire recorded history** (11 runs for the genotype
one alone) and it has zero corpus coverage.

**Settled 2026-07-30: this is a task family.** It has no dedicated op and does not need one. It
**composes existing ops** — a search op to pull the values, then the batch-upload chain to write
the mapping into a sheet — with the clustering judgement supplied by the agent in between. It
earns a family because it is a **feature we want consistently tested**, not because it maps to an
op boundary.

*(No `harmonize` / `normalize` / `merge` / `dedup` op exists among the 20 bins. Recorded as a
fact, not as a gap to close.)*

**Which ops it touches:** UNCONFIRMED — no CC run with a `tool_use` trace has ever been captured
for this family. Expected shape from the 07-29 probe's behaviour is search → agent clustering →
`project-resolve` / `sampletype-attrs` / `build-payload`. Worth capturing on the first real run
rather than assuming.

**Routing tell:** the user points at *values that look alike*, not at records. Contrast with
`sample_search` ("find me X") and with `batch_upload_preparation`, which is the step *after*
harmonization: harmonization decides the mapping, batch-upload writes it into a sheet.

**Verified working:** the 2026-07-29 probe ran the full three-turn sequence and it landed. It
found the 195 NDMA mice, produced a correct genotype breakdown, proposed a normalization, drew the
write boundary unprompted, and prepared 188 update rows before stopping on a stated blocker rather
than fabricating a workbook.

**Verified targets, with exact counts, straight from `seek_production`.** These are ready-made
expected values:

| attribute | near-duplicate cluster | counts |
|---|---|---|
| `Species` | `Macaca mulatta (Rhesus)` / `Macaca mulatta` / `Rhesus macaque` | 163 / 106 / 73 = **342 split 3 ways** |
| `Species` | `Mycobacterium tuberculosis` / `Myobacterium tuberculosis` | 15 / 4 — a straight **typo** |
| `Species` | `mus musculus` / `Mus musculus` / `Mouse` | 44 / 26 / 5 — case plus synonym |
| `Lab` | `Fortune` / `Fortune Lab` | 1589 / 964 = **2553 split** |
| `Lab` | `Essigmann` / `Essigmann lab` / `Essigmann Lab` / `ESSIGMANN_lab` | 205 / 48 / 37 / 21 = **311 split 4 ways** |
| `Scientist` | `Edward B. Irvine` / `Eddie Irvine` | 379 / 369 — near-equal, so **which is canonical is genuinely ambiguous** |
| `Scientist` | `JoAnne Flynn` / `Joanne Flynn` | 14104 / 325 |
| `Scientist` | `Tricia Darrah` / `Patricia Darrah` | 372 / 228 |
| `Lab` | `Sassetti Lab` / `Sassetti` | 42 / 32 — a **second** near-equal ambiguity, cheaper than Irvine |
| `Lab` | `Alter Lab` / `Alter` | 601 / 9 |
| `Strain` | `C57BL/6J` / `C57BL6` | 61 / 44 |
| `Cohort` | `4wk` / `4 week` | 237 / 2 — **and `4wk_Day1` 8 + `4wk_Day2` 8**, so the total is 239 or 255 depending on whether day-level cohorts count. Decide before asserting. |
| `Genotype` | `RG` / `RGA` / `RGATG` vs the long `RaDR+/+; GPT+/+` forms | see the 2026-07-29 probe mapping |

The `Edward B. Irvine` / `Eddie Irvine` pair is the best single test in the set: near-equal counts
means a system that just picks the larger cluster gets it wrong for the right-sounding reason.

**Your real questions (5 distinct, 17 runs):**

| runs | question |
|---:|---|
| 11 | I noticed that some of these genotype terms look similar, could you attempt to normalize them? |
| 3 | Please list all mice associated with NDMA and create a histogram stratified by genotype. |
| 1 | Please create an update sheet for re-ingestion of these normalized genotypes. |
| 1 | erk -- ok ... the LONG version is the correct naming, 'RGA' is very incorrect. Harmonization should discard that for the long form. |

That last one is worth keeping verbatim: it is a **correction turn**, where you told the system its
first mapping was backwards. Nothing in the corpus tests whether a correction is absorbed.

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |

---

### 3. `pipeline_output_reingest`
> Register the outputs of a finished nf-core / Luria run as new `A.*` analysis samples by
> listing the run directory and building a reviewable workbook.

**Routing tell:** the user points at a finished run, a run directory, or "the outputs of the run
I just launched". Distinct from `pipeline_build_and_launch` (NS), which is about *starting* a run.

**Verified working:** both ops exist and are wired — `nextseek-run-ls` SSHes Luria for `ls -laR`,
`nextseek-build-upload-xlsx` renders one 4-sheet workbook per SampleType with a QA verdict. Luria
credentials are set. Two real run directories hold real outputs.

**BLOCKED — do not author beyond a route gate yet.** This has never once completed: killed by the
$0.50 budget cap, then by 180s, then by 300s. Raising the wall bought nothing, which is the
signature of work looping rather than work marginally over. The local `docker/nextseek.env` has no
timeout override, so the 180s default is in force.

**Also note:** these three ops (`run-ls`, `build-upload-xlsx`, `pipeline`) do not exist in the
older tree the prior op inventory was built against, which is why they appear in no per-op table.

**Your real questions (5 distinct, 14 runs):**

| runs | question |
|---:|---|
| 7 | Can you create an update sheet for re-ingestion? |
| 3 | Build me a NExtSEEK reingest upload sheet from the pipeline outputs in /net/bmc-pub10/.../nfcore_rnaseq_260723_205359_0 |
| 2 | The scrnaseq run at /net/bmc-pub10/.../nfcore_gideon-4wk_260711_024438_0 has finished, register its outputs as new A.SCXP analysis samples. |

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

---

### 4. `open_ended_analysis`
> Analytical or comparative reasoning over data rather than retrieval of it.

**Routing tell:** the user wants a judgement, a comparison, a grouping or a distribution, not a
result set.

**A finding you need to decide on.** Your single largest CC demand here is histograms and plots
(13 runs). The image installs **polars, xlsxwriter, orjson** — and **not** matplotlib, scipy,
seaborn, plotly or pandas. So "make a histogram" can only ever be answered as **text**, not as a
rendered image. The 2026-07-29 probe did exactly that and it looked like a success: it returned
`RG 44, RGATG 39, RaDR R/R; gpt g/g 33 …`, which I reproduced exactly from MySQL. Correct numbers,
no picture.

**Decide before authoring:** is a text breakdown the intended answer, or should the image ship
matplotlib? The criteria differ completely.

**Your real questions (12 distinct, 15 runs):**

| runs | question |
|---:|---|
| 3 | Please list all mice associated with NDMA and create a histogram stratified by genotype. |
| 2 | Create a histogram of mice treated with NDMA stratified by genotype. |
| 1 | Cluster these samples by their metadata and tell me what the groups have in common. |
| 1 | Is treatment A significantly better than treatment B based on our sequencing results |
| 1 | Good but make the histogram bars yellow instead of blue. |

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

---

### 5. `entity_write_via_api`
> Create, update or delete an Investigation, Study, Project or Assay through the REST API.

**Routing tell:** the user names a single entity and an action on it. Distinct from
`batch_upload_preparation`, which builds a reviewable sheet and writes nothing.

**Verified working:** `nextseek-api-write` is the only live-mutation op. Without an explicit
`--confirmed-write` it exits 5 `WRITE_BLOCKED` before any network call, and two further gates
refuse independently. The skill also requires a plain-text confirmation to the user first.

**Author as a confirmation gate, not a write.** The safe assertion is that the agent proposes the
write and asks, and mutates nothing on turn 1. If a case ever passes `--confirmed-write` it
changes the live database.

**Careful:** the retired corpus holds ~15 near-identical `write.create_*` variants. Do not
recreate that. One or two cases is the right number.

**Your real questions:** none in the ad-hoc set. All prior write cases came from the corpus and
were retired.

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

---

### 6. `export_and_file_delivery`
> *(renamed from `file_io_and_summarization`)*
> Produce a file the user takes away: a spreadsheet, a CSV, an export of a result set.

**Why the rename.** The shipped description is written against `/data/projects/`, which
**is never mounted**. The real read-only mounts are `/data/input` and `/data/shared`, and the
writable path is `/data/scratch`. Both `route_capabilities.json:80` and the agent's own
`container/CLAUDE.md:3` carry the wrong path, so any question phrased around `/data/projects`
fails on a documentation bug rather than a capability. **Fix those two files.**

What your usage actually shows is export, not project-file reading.

**Reachable with no preconditions:** the baked catalog tree at `/app/plugins/nextseek/context/`
(101 sample types, 217 assays, 40 endpoints) and `/app/docs/nextseek/`.

**Your real questions (3 distinct):**

| runs | question |
|---:|---|
| 1 | Export all metadata for sample NHP-220630FLY-1-PUB and all of its derived (descendant) samples to Excel. |
| 1 | Export all samples in the database to a spreadsheet. |
| 1 | write a csv summarizing those samples, uid, sample type, project, etc |

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

---

### 7. `code_and_scripts`
> Write, run or refactor code against NExtSEEK data.

**Routing tell:** the user asks for a script, a conversion, or automation they will reuse.

**Constrained:** the container runs the stock Claude Code toolset (Bash, Read, Write, Edit, Glob,
Grep) writable only at `/data/scratch`, with Python 3.14 and **polars, not pandas**. Ask for
polars-shaped work or for code alone.

**A note on your existing three:** they are infra probes ("Use bash to echo FRESHTEST-OK"), which
test the container rather than the product. Keep at most one as a liveness check and write real
ones for the rest.

**Your real questions (4 distinct):**

| runs | question |
|---:|---|
| 1 | Write and run a Python script that pulls the published samples from NExtSEEK and saves to a file the UIDs of any that are missing an organism value. |
| 1 | Create a file at /data/scratch/proof.txt whose entire contents are exactly: DEPLOYED-OK-… *(infra probe)* |

> **Corrected 2026-07-30.** I previously said `Organism` is "NULL on all 50,887 samples". Wrong:
> `JSON_CONTAINS_PATH(json_metadata,'one','$.Organism')` returns **0**, so the key does not exist
> at the sample level *at all*. `JSON_EXTRACT` returns SQL NULL for a missing path and I read that
> as a null value. There is also no `organism` column and no matching `sample_attributes` title;
> `organisms` exists only on Assay/Study/Investigation. `Species` is the nearest real field, on
> **690** of 50,887. So the agent has no field to check and will fail or silently substitute
> `Species`. **Do not write a criterion around 50,887.**

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

---

### 8. `ambiguous_study_resolution`
> Reconcile a named study or cohort across stores when the graph alone cannot settle it.

**Routing tell:** the named scope is not a known investigation and reads like a descriptive cohort
or timepoint ("the CD8 depletion study", "a 4 week study"), or the user says a previous study
answer looked wrong.

**Verified real, and narrow by design.** The shipped description explicitly says the graph path now
resolves Study and Investigation titles itself, so only the metadata-attribute case belongs here.
Both of your questions are genuine instances:
- `CD8 Depletion` exists as a **`Study` attribute (15 samples)** and a **`Cohort` value (15)** — not
  a graph node.
- `4 week` lives in **`Cohort`**, with 2 samples exactly and **237 more under `4wk`**. You already
  ruled the right answer is 239, i.e. search should harmonize.

**Worth knowing:** `green.refine_recall`'s seed routed `container_cc` and the router cited this
family by name, which suggests the case belongs here rather than in the green set.

**Your real questions (3 distinct):**

| runs | question |
|---:|---|
| 1 | Which samples belong to the CD8 depletion study? I don't think it's a project. |
| 1 | Find me monkeys with the 4 week study attribute |
| 1 | Try that search again but the study as "4 week" |

**Add yours:**

| # | question | expected reply contains | notes |
|---|---|---|---|
| 1 | | | |

---

## Route gate: rebuild it

All three CC gates were retired, so nothing catches a CC question being sent to NS. One gate per
family, minimum.

**Cost warning, verified.** A `route_gate` case is **not** free. `services/cc_assistant.py:347`
emits `route_decided` and then falls straight through into the engine; only the `unrelated` route
returns early with canned text. The harness merely stops polling while the server completes the
turn on a daemon thread with no cancel path. And because cost is read from `query_complete`, which
route-tier polling never sees, the manifest records `None` and the run prints `$0`.

So budget the gate set at **one full Opus turn per case** (~$0.20, 75-200s), not zero. The comment
at `nessie_tests/runner.py:126-128` says otherwise and is wrong.

| # | family | question | assert |
|---|---|---|---|
| 1 | batch_upload_preparation | | `route eq container_cc`, `route_source eq baml` |
| 2 | harmonization | | same |
| 3 | pipeline_output_reingest | | same |
| 4 | open_ended_analysis | | same |
| 5 | entity_write_via_api | | same |
| 6 | export_and_file_delivery | | same |
| 7 | code_and_scripts | | same |
| 8 | ambiguous_study_resolution | | same |

---

## Open decisions

1. **Histograms:** text breakdown, or install matplotlib? Blocks `open_ended_analysis` criteria.
2. **Artifact keys:** fix `evaluate.py` to read `artifacts[].label` and `cc_raw_files`? Until then
   no CC case can prove a file exists.
3. **The 180s cap:** reingest cannot pass end to end without it. Raise, or keep reingest at
   route-gate only?
4. **Write path:** is exercising `entity_write_via_api` on the dev box acceptable, given the server
   completes the turn behind the abandoned poll?
5. ~~A harmonization op.~~ **Settled 2026-07-30:** harmonization is a task family, not an op.
   It composes existing ops plus agent judgement, and earns its place because it is a feature
   we want consistently tested. No new op required.
6. **Family names:** `export_and_file_delivery` proposed over `file_io_and_summarization`. Keep,
   or leave the shipped name and just fix its description?
