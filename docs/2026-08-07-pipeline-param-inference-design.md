# Species and library-fact inference from NExtSEEK metadata

Date: 2026-08-07
Status: approved design, revised after the acceptance audit, not yet planned
Scope: sub-project 1 of 3 (see "Relationship to the other two projects")

> **Revised 2026-08-07 after running `scripts/audit_metadata_coverage.py` (never committed)
> against the live database.** The audit confirmed the core premise and
> falsified three parts of the original design; those are now explicit
> non-goals, and one component the original design lacked — a normaliser — is
> now the single highest-value piece. Numbers are in the appendix.

## Problem

The nf-core pipeline agent's `configure_run` derives almost nothing from the
sample metadata it already holds. The single exception is species, and it is
derived badly: `tool_resolve_samples` scans every flattened metadata value and
counts a vote only when the value is an exact, case-insensitive member of a
14-entry synonym table (`reference_bundles.json` → `species_to_bundle`).

Measured against the live corpus, that resolves **55.9%** of `D.SEQ` samples.
The other **44.1%** resolve to `null` and reach `luria/submitter.py`, where
`run_genome = genome or "GRCh38"` silently aligns them to the human genome. The
warning that fires goes to the container console, not to the conversation.

The two largest causes are both fixable:

- The `MUS` sample type — mouse — declares no `Species` attribute at all. It has
  `Strain` and `Genotype`. A mouse cohort therefore cannot produce a vote, and
  the species is carried by the *sample type itself*, which nothing reads.
- **545 samples (26.5% of the corpus) record `Species = "Macaca mulatta
  (Rhesus)"`.** A further 92 record `"Macaca mulatta"`. Same animal; the first
  group fails to resolve solely because of the parenthetical.

The failure mode matters more than the inconvenience: a wrong reference genome
does not error. It produces a complete, plausible-looking result that is wrong.
That is the same class of harm the codebase already refuses to accept for CRISPR
guides and Hi-C digestion protocols, where `seqera/user_params.py` fails closed
and asks rather than guessing.

## Goals

1. Derive species and therefore the reference genome reliably, for all 31
   catalogued pipelines. Target: 92.1% resolved, 7.9% asked, 0% guessed.
2. Extract library facts — `strategy`, `platform`, `molecule`, `layout` — from
   the controlled-vocabulary fields `D.SEQ` already carries, for the data-type
   fit check and for sub-project 2.
3. Never apply an inference on weak, partial or conflicting evidence. Ask
   instead, through the elicitation channel that already exists.

## Non-goals

Three of these were in the original design and were cut by the audit. The
measurements are in the appendix; each should be revisited if the corpus
changes; `scripts/audit_metadata_coverage.py` was never committed, so re-measuring
means writing it again.

- **Assay-driven behaviour params (`inferred_params` mappings).** Cut. With the
  `kit` fact dead (below), primers still elicited by policy, and strandedness
  deliberately left at nf-core's `auto`, there is no param left with real
  metadata signal for any of the four pipelines exercised on Luria. Deferring
  this removes a JSON block format, an applier module and a schema test that
  would have had nothing to validate.
- **The `kit` fact and scrnaseq `protocol` inference.** Cut. `ExpressionKit` is
  populated on **2 of 2,057** samples (0.1%). 10x chemistry is not recorded.
- **`layout` → `single_end`.** The `layout` fact is still extracted (it is free,
  and sub-project 2 may want it), but it maps to no param: **every sample in the
  corpus is paired-end.** All 2,055 populated `LibraryDesign` values are a
  spelling of "Paired". The mapping would have zero discriminating power.
- **Inferring the nine currently-elicited values** (ampliseq primers, CRISPR
  guides, Hi-C digestion, viralrecon platform/protocol, nanoseq protocol,
  bactmap reference, magmap genomeinfo, metatdenovo ORF caller, smrnaseq
  mirtrace species). These stay fail-closed; `user_params.py`'s reasoning is
  unchanged by this work.
- **Inferring strandedness.** nf-core rnaseq accepts `auto`, which beats a
  metadata guess.
- Pipeline selection, and reingest of results. Separate sub-projects.

## Decisions taken during design

| Question | Decision |
|---|---|
| What is inferred | Species/reference, plus library facts for the fit check. Not behaviour params (audit), not the elicited values (policy). |
| Trust model | Tiered by evidence strength. Exact/strong applies and reports its source; partial/weak/conflict asks. |
| Cohort disagreement | Stop and ask, showing the split. Never pick a majority winner. |
| Coverage | All 31 pipelines, since species is pipeline-independent. |
| Architecture | Deterministic extractor over the lineage flatten. Not prompt-driven. |

Approach C — exposing the fields and writing prompt rules — was rejected because
it contradicts the precedent already set in `user_params.py` ("a prompt
instruction can be forgotten mid-conversation") and because it cannot be
unit-tested. Approach B — a single inference pass inside `configure_run` — was
rejected because it cannot be reused by sub-project 2, which needs these facts
*before* a pipeline exists.

## Architecture

Three new modules under `chat_nextseek/src/chat_nextseek/inference/`, which never existed --
this design was approved but never built. No new
module in `seqera/` — the consumers are existing functions.

```
resolve_samples ─▶ rules.py ─(normalise.py)─▶ FactSet ─▶ state["facts"]
                                                 │
                                                 ├─▶ (sub-project 2)
                                                 ▼
configure_run   ─▶ species → bundle  +  fit check  +  questions
                                                 │
                                   questions ────┴─▶ ask_the_user
```

Facts are computed once, at resolve time, where the lineage flatten already
exists and before a pipeline has necessarily been chosen. `configure_run`
consumes them and never re-derives them.

### `inference/normalise.py` — the highest-value component

The audit's central finding is that these fields are **free text, not a
controlled vocabulary**, and that matching them exactly is the mistake the
current code makes. Normalisation is three ordered steps:

1. **Case-fold and trim.** Collapses `GENOMIC`/`Genomic`, `PAIRED`/`Paired`.
2. **Strip trailing parentheticals and bracketed annotations.**
   `Macaca mulatta (Rhesus)` → `macaca mulatta`. This one rule recovers 545
   samples — 26.5% of the corpus — and is the difference between 65.6% and
   92.1% bundle resolution.
3. **Synonym map**, per fact vocabulary. Small and explicit: `rnaseq` →
   `rna-seq`, `mus musculus` → `mouse`.

Normalisation is a separate module rather than inline string handling precisely
because it is load-bearing: it needs its own tests, and every rule routes
through it.

### `inference/facts.py` — vocabulary

A `Fact` is `{name, value, source, strength, uids}`:

- `name` — `species`, `strategy`, `platform`, `molecule`, `layout`.
- `value` — the normalised conclusion (`"mouse"`, `"illumina"`, `"rna-seq"`).
- `source` — the field it came from, displayable: `"sample_type:MUS"`,
  `"D.SEQ.LibraryDesign"`, `"NHP.Species"`.
- `strength` — `exact` > `strong` > `weak`, an ordered enum, plus the separate
  flag `partial`. `partial` is not a rung on that ladder: it means the evidence
  is sound but incomplete (`NHP` establishes macaque but not *which* macaque).
  A partial fact never applies, whatever strength produced it.
- `uids` — the leaf UIDs that supported this value.

A `FactSet` aggregates facts across a cohort's leaves, answers "do these samples
agree on `<name>`?", and exposes `questions()` — the unresolved, partial and
conflicting facts, ready for rendering.

### `inference/rules.py` — extraction

Pure functions over the flattened lineage dict `tool_resolve_samples` already
builds. Rules run in strength order per fact name and stop at the first `exact`
hit. Every comparison goes through `normalise`.

Species rules, nearest ancestor first:

| Rule | Field | Strength | Audit |
|---|---|---|---|
| `species_from_sample_type` | the sample type itself | exact for `MUS`; **partial** for `NHP` | fires on 9.7% |
| `species_from_species_field` | `Species` (CEL, BAC, NHP, VIR) | strong | fires on 82.4% |
| `species_from_strain` | `Strain`, `Genotype` (MUS, CEL, BAC, VIR) | weak | corroborates; never concludes alone |
| `species_from_taxonomy_id` | `TaxonomyID` (CEL, BAC, VIR) | exact | **populated on 0 samples today.** Kept as three lines of forward-looking insurance, and documented as currently dead |

Rules produce a *species*. The existing `species_to_bundle` table still maps
species → genome bundle; that logic is not duplicated, but its lookups now go
through `normalise`.

Library fact rules:

| Fact | Source field | Populated | Rule |
|---|---|---|---|
| `strategy` | `LibraryStrategy` | 100% | 7 distinct values after folding: amplicon, rna-seq, wgs, scrna-seq, targeted capture, … |
| `platform` | `Sequencer` | 100% | Illumina / Nanopore / PacBio / Singular family matching over 21 distinct raw values |
| `molecule` | `ExtractedMolecule` | 65.6% | DNA / total RNA / polyA RNA |
| `layout` | `LibraryDesign` | 99.9% | paired / single. **Not** `SequencingType`, which the audit showed holds modality (`Illumina Sequencing`, `DNA barcoding`), not layout |

`D.SEQ` carries **both spellings** — `ExractedMolecule` in Standard Metadata and
`ExtractedMolecule` in Possible Metadata Fields. The rule reads both.

The `platform` rule supersedes `emitter.py`'s `_PLATFORM_HINTS` value-blob scan,
which contains a live defect: `"\bont\b"` is written as a non-raw string, so its
runtime value is `'\x08ont\x08'` and the Oxford Nanopore hint can never match.

### Trust tier → behaviour

| Strength | Behaviour |
|---|---|
| `exact` | applied, source named in the confirmation message |
| `strong` | applied, source named |
| `partial` | not applied — targeted question (the `NHP` case) |
| `weak` | not applied — question, with the weak reading offered as the suggested answer |
| conflict across the cohort | not applied — question showing the split and the supporting UIDs |

Disagreement is detected per fact name by grouping values across the cohort's
leaves; more than one distinct value is a conflict. The fact retains every value
and its supporting UIDs so the question shows the real split — "7 samples say
mouse (D.SEQ-…-PUB, …), 2 say human (…)".

A user's answer returns through `configure_run`'s `params` argument and lands
after everything else in the merge, so a human answer always beats an inference.
No new override mechanism.

### Data-type fit check

The `strategy` and `molecule` facts give the "is this pipeline right for these
samples?" judgement something deterministic to stand on. Today that check lives
entirely in the prompt (step 3: *"Judge data-type fit from the returned leaves'
sample_type/assay/metadata"*) and is pure model reasoning.

`configure_run` compares the cohort's `strategy` fact against the pipeline's
declared acceptable strategies and surfaces a mismatch as a **question**, not a
silent param: `LibraryStrategy=amplicon` against `rnaseq` should stop and say so.

The catalog already carries an `accepted_assay_patterns` field on every entry
that no runtime code reads — it is exercised only by
`tests/test_catalog_enrichment.py`. This check is what that field was for.
Repurpose it, renaming to `accepted_strategies` if the regex shape does not fit
the 7-value vocabulary the audit found. Reviving dead data is in scope; a
broader catalog refactor is not.

## Integration points

| File / function | Change |
|---|---|
| `agent_tools.py` · `tool_resolve_samples` | After the per-leaf flatten, run the rules; aggregate into a `FactSet`; **replace** the species-vote block with `facts["species"] → resolve_bundle_for_species`. Write `state["facts"]`; keep `detected_species` / `bundle_key` populated for compatibility. Add a facts summary to the returned JSON so the model can narrate it. |
| `agent_tools.py` · `tool_configure_run` | After the existing user-param gates, collect `FactSet.questions()` plus any fit-check mismatch. Questions merge into the existing `needs_user_input` / `ask_the_user` payload. Success payload names the inferred species and its source. |
| `user_params.py` | New `render_ambiguity()` beside `render_elicitation()`, same voice and format, so declared-required-param questions and inference-gap questions read identically to the user. |
| `emitter.py` · `_platform_from_meta` | Becomes a consumer of the `platform` fact instead of re-scanning a value blob. Fixes the dead `"\bont\b"` hint. |
| `reference_bundles.json` · `species_to_bundle` | Lookups route through `normalise`. The table itself can shrink — case and parenthetical variants no longer need enumerating. |
| `prompts/pipeline_agent.txt` step 6 | **Narrowed.** The instruction telling the model to infer the organism itself when `detected_species` is null must be removed, or the model will keep guessing and undercut the mechanism. It relays questions; it does not conclude species. |
| `luria/submitter.py:148` | `run_genome = genome or "GRCh38"` becomes a hard failure. With inference in place an unresolved species cannot reach submit — the question blocks it first. This is the last path by which a wrong species reaches the cluster. |
| `agent_tools.py` · `dispatch_pipeline_tool_call` | Fix `pipeline_key` precedence: it resolves `state.get(...) or tool_input.get(...)`, so state wins and a `resolve_samples` issued after a first `write_samplesheet` silently filters on the previous pipeline's accepted leaf types. Facts are pipeline-independent and therefore safe, but this design makes `resolve_samples` more central and encourages exactly the re-resolve that is broken. One-line fix plus a test. |

New session state key:

```
state["facts"] = {
  "species":  {"value": "macaca mulatta", "strength": "strong",
               "source": "NHP.Species", "uids": ["D.SEQ-…", …]},
  "strategy": {"value": "amplicon", "strength": "exact",
               "source": "D.SEQ.LibraryStrategy", "uids": [...]},
  ...
}
```

## Error handling

Governing rule: **rules are total functions.** They return facts or nothing;
they do not raise.

- A blank, absent or sentinel field (`R_bp = "N/A"`, `Species = "unknown"`)
  produces **no fact** — never a default one. Absence of evidence must stay
  distinguishable from evidence of absence, or the tiering collapses.
- A rule that raises anyway is caught per-rule and logged; that fact is absent.
  `tool_resolve_samples` already uses this pattern for its summary build. One
  broken rule must not take down sample resolution.
- A normalised value with no synonym-map entry is returned as-is, not dropped.
  It then fails the bundle lookup and becomes a question, which is correct.
- **Questions are batched.** Everything one pass finds ambiguous goes into a
  single `ask_the_user` block. Serial questions would consume the
  `MAX_ITER = 12` budget and read as an interrogation.
- A param the user has explicitly answered is skipped on subsequent passes, so
  an answered question is never re-asked.

## Testing

No model in the loop at any level — that is the payoff of the chosen approach.

| Level | Coverage |
|---|---|
| `normalise` | Case variants; `Macaca mulatta (Rhesus)` → `macaca mulatta`; bracketed annotations; synonym map; a value with no mapping passes through unchanged |
| Rules | Table-driven fixtures over real-shaped metadata dicts. Must include `MUS` with no `Species`; `NHP` with no `Species` (partial); both `ExractedMolecule` and `ExtractedMolecule` spellings; blank and sentinel values |
| `FactSet` | Agreement; disagreement with supporting UID lists; partial evidence; `questions()` output |
| `configure_run` | Exact/strong evidence → applied and reported; conflict → `ok=false` with questions and **nothing written to disk**; user answer → applied and not re-asked; strategy/pipeline mismatch → question |
| Regression | Existing species tests still pass, plus the two cases that fail today: a `C57BL/6J` mouse cohort resolves to `GRCm39`, and `Macaca mulatta (Rhesus)` resolves to `Mmul_10` — neither silently to `GRCh38` |
| Submitter | An unresolved genome raises instead of defaulting to human |

## Appendix — acceptance audit, 2026-08-07

`scripts/audit_metadata_coverage.py` (never committed), run against the live database.
51,359 samples; 2,057 `D.SEQ`.

**Field population on `D.SEQ`:**

| Field | Populated | |
|---|---|---|
| `LibraryStrategy`, `Sequencer`, `SequencingType`, `LibrarySource`, `LibrarySelection` | 100% | |
| `LibraryDesign` | 99.9% | all values are a spelling of "Paired" |
| `F_bp`, `R_bp` | 85.2% | |
| `ExtractedMolecule` | 65.6% | |
| `ExpressionKit` | **0.1%** | 2 samples — killed the `kit` fact |
| `TaxonomyID` | **0%** | never populated |

**Species resolution, current vs proposed:**

| | Resolved | Falls through to GRCh38 |
|---|---|---|
| Current exact-match vote | 55.9% (1,150) | **44.1% (907)** |
| Proposed rules + normaliser | **92.1% (1,894)** | 0% — 7.9% (163) ask instead |

**Species values found, and why the parenthetical matters:**

| Count | Value | Case-fold only |
|---|---|---|
| 971 | `Macaca fascicularis` | → `Mfas6.0` |
| **545** | `Macaca mulatta (Rhesus)` | **no bundle** |
| 199 | mouse (via `MUS` sample type) | → `GRCm39` |
| 92 | `Macaca mulatta` | → `Mmul_10` |
| 44 + 18 | `Homo sapiens`, `Homo Sapiens` | → `GRCh38` |
| 25 | `Mus musculus` | → `GRCm39` |

All 545 unmapped samples are `LibraryStrategy = amplicon`.

**Lineage reachability:** 95.4% of `D.SEQ` samples reach an organism sample type
(`MUS`, `NHP`, `CEL`, `BAC`, `VIR`); 4.6% have no organism ancestor at all.

## Relationship to the other two sub-projects

2. **Pipeline selection from intent** — a front door before `resolve_samples`,
   for users who do not know which pipeline they want. It consumes the same
   `FactSet`: the audit found `LibraryStrategy` populated on 100% of `D.SEQ`
   with roughly 7 distinct values, which is a strong pipeline vote and the main
   reason this sub-project is built first. It also requires inverting the
   current ordering, in which `pipeline_key` gates which sample types
   `resolve_samples` will return at all.
3. **Reingest of pipeline results into NExtSEEK** — hardening an existing path,
   not greenfield: `granular._run_ls` reads a remote run directory,
   `_build_upload_xlsx` composes rows into a 4-sheet workbook, and
   `reingest_qa.py` grades them CLEAN / SOFT_FLAG / HARD_REJECT.
