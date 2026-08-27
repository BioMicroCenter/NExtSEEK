# Luria-only pipeline launch: local-path / SRR file resolution, fetchngs pre-stage, and local-reference bias

Date: 2026-07-23
Issue: #2 (reframed)
Status: design, approved for planning
Scope: `chat_nextseek` pipeline agent + Luria launch backend (`seqera/`, `pipeline/`, `luria/`)

## 1. Motivation

The nf-core launch backend currently synthesizes remote ENA fastq URLs into the
samplesheet and targets Seqera Tower by default. On MIT Luria this fails: nf-core
rejects the remote URLs (SLURM job 11205951 died in schema validation), and the
Luria path only works today because a fully populated Tower env happens to be
present to satisfy a hidden gate in the emitter.

We are making the pipeline **Luria-only** and giving it two honest file sources:
a local Luria path or an SRA accession. We are also fixing a truth-in-messaging
bug: the agent warns about an "iGenomes fallback" reference even when a local
Luria reference for that genome exists and will actually be used.

## 2. Goals and non-goals

### Goals
1. Resolve each sample's fastqs from exactly two sources: a local Luria path
   matching `/net/bmc-*` (or any local `*.f[ast]q.gz`), or an SRR/ERR/DRR
   accession. Local path present wins and skips any fetch.
2. For SRR-only samples, run an `nf-core/fetchngs` pre-stage on Luria (inside the
   single `run.sh`, before the unchanged pipeline block) that downloads to a
   shared cache and fills the samplesheet in place.
3. Abandon the ENA URL route. Turn it off and strip user-facing and LLM-facing
   mention; leave `seqera/ena.py` physically in place.
4. Abandon Tower/Seqera as a launch target. Luria becomes the only exposed mode.
   Sever the emitter's Tower-completeness gate so Luria gets its launch artifacts.
   Leave the Tower code physically in place.
5. Bias reference resolution to the local Luria reference registry
   (`LURIA_GENOMES`) first, falling back to iGenomes / nextflow standard only when
   there is genuinely no local ref, with messaging that tells the truth.

### Non-goals
- Deleting Tower or ENA code. Both stay in the tree, dormant and unmentioned.
- Supporting non-`D.SEQ` leaf sample types (unchanged from today).
- Concurrency safety on the shared cache across simultaneous runs. Single
  operator per working path is assumed; noted as a caveat.
- Reviving the dead `configured` / `store_root` reference path. Its latent Luria
  genome-key bug is flagged in section 9 but is out of core scope.
- Any change to the pipeline block of `run.sh` itself. It stays byte-identical.

## 3. Architecture: a host / Luria split

The container (host side) decides the source per sample and builds a samplesheet
where local rows are fully populated and SRR rows are left blank but tagged with
their accession. Luria side, `run.sh` fills the blanks via fetchngs, then runs
the pipeline unchanged.

```
HOST (container)                          LURIA (run.sh, one sbatch)
-----------------                         --------------------------
resolve_samples                           [fetch pre-block, rendered only if the
  per leaf: local /net/bmc-* path?          sheet has SRR-only rows]
    yes -> fill fastq_1/2 (local row)       1. awk: accessions where fastq_1 empty
    no  -> SRR accession -> blank row           -> ids.csv
write_samplesheet (ENA off)                 2. nf-core/fetchngs --input ids.csv
  -> samplesheet.csv: mixed rows                 --outdir <WORKING>/fastq_cache -resume
configure_run (Tower gate severed,          3. fixed csv fill: blank rows <- cache
  reference bias applied)                        paths by <SRR> filename convention
  -> params.yml + minimal launch.yml       ---------------------------------------
submit_to_luria -> stage + sbatch          [pipeline block, LITERALLY UNCHANGED]
                                             nextflow run <pipeline> --input samplesheet.csv ...
```

Key property: the pre-block **rewrites `samplesheet.csv` in place**, filling
`fastq_1`/`fastq_2` only for rows that carry an accession and have empty fastq
columns. Local rows already carry their `/net/bmc-*` paths and are untouched. The
pipeline block's `--input samplesheet.csv` never changes. For a pure-local
cohort the fetch pre-block is not rendered at all, so `run.sh` is byte-identical
to today.

## 4. Thread 1: file-source resolution (host)

Today file discovery is coupled to accessions. `_fastq_from_meta`
(`seqera/emitter.py:227-254`) already scans a leaf's metadata for a local path and
prefers a local absolute path over a URL, but it only runs inside the `if acc:`
branch (`emitter.py:573-586`) and under a single-run guard, so a sample with a
local path and no accession is invisible.

Changes:
- Lift `_fastq_from_meta` out of the `if acc:` block and the single-run guard so
  any leaf yielding a local path fills its row, accession or not. Add an explicit
  `/net/bmc-*` recognizer alongside the existing `startswith("/")` local-path
  preference (`emitter.py:253-254`).
- In `tool_resolve_samples` (`pipeline/agent_tools.py:230-356`), key the curated
  file-path metadata by **sample UID**, not only by accession
  (`agent_tools.py:299,308-311,336`), so a path-only sample survives into the
  emitter. `write_samplesheet` passes this UID-keyed map to the emitter
  (`agent_tools.py:443`).
- Row validation `_validate_rows_against_resolved` (`agent_tools.py:369-394`)
  must accept a pure-local-path row (it has a UID; no accession required).

Result per row after host build:
- Local sample: `sample` (leaf UID), `fastq_1`/`fastq_2` set to the local path,
  plus `strandedness`/`cohort`/enrichment columns.
- SRR-only sample: same columns, `fastq_1`/`fastq_2` empty, `accession` set.

## 5. Thread 2: ENA route off (host, code left in place)

- `write_samplesheet` stops calling `resolve_accessions`
  (`pipeline/agent_tools.py:430-431`); pass `resolutions=[]` so the emitter's
  `acc_to_runs` map is empty and no ENA URLs are synthesized
  (`emitter.py:556-592`).
- Flip the emitter's drop-on-unresolved behavior: today a row whose accession did
  not resolve via ENA is dropped (`emitter.py:577-578`). With ENA off, an SRR row
  with no local path must instead be **emitted blank and tagged** as a fetch
  target, not dropped.
- Leave `seqera/ena.py` intact. Remove only its call sites
  (`agent_tools.py:34` import, `:431`).

## 6. Thread 3: Tower off and severing the hidden gate (host, code left in place)

This is the largest structural change. Today `emit_launch_artifacts` and
`emit_nfcore_artifacts` write `params.yml` / `launch.yml` only when
`tower_complete` is true (`emitter.py:361-366`, `:611-615`). The Luria submitter
requires `artifacts["launch"]` to exist (`pipeline/agent_tools.py:560-561`), so a
Luria-only deployment currently still needs a full Tower env just to produce the
launch artifact.

Changes:
- Remove `submit_to_tower` from `build_pipeline_tool_schemas`
  (`agent_tools.py:194-195`) so it is not exposed. Leave `tool_submit_to_tower`
  and the Tower client code in place.
- Give the launch-artifact emission a Luria path that does not depend on
  `tower_complete`. `configure_run` (`agent_tools.py:511-521`) must produce
  `params.yml` plus a minimal `launch.yml` carrying only `name`, `pipeline`,
  `revision` (the only fields the Luria submitter consumes,
  `luria/submitter.py:97-99`). The cleanest implementation is a Luria-only emit
  branch that bypasses the `tower_complete` gate in the emitter.
- Default and only launch mode is `luria`. `detect_pipeline_launch_mode`
  (`config.py:36-43`) default flips from `tower` to `luria`. The `{launch_mode}`
  prompt hint and the "Choosing a submit tool" rule collapse to Luria.
- Strip Tower/Seqera/ENA mention from `prompts/pipeline_agent.txt` (`:7-8,15,18,22,24,28-31,37`),
  the `notes.md` builder `_build_notes_md` (`emitter.py:305-318`, the "Tower /
  Seqera" and "Accession resolution (ENA)" sections), and the tool descriptions
  that reference "the ENA layer" / "Tower submission YAMLs"
  (`agent_tools.py:52,80,107,128-134`). The legacy `prompts/seqera_agent.txt` and
  the reporter NFCORE note (`agents/reporter.py:279-288`) are already dead for
  launches (orchestrator routes NFCORE to `pipeline_agent.start()`,
  `orchestrator.py:607-609`); de-mention them but do not delete.

## 7. Thread 4: reference bias and honest messaging (host + prompt)

The user-facing reference message is **emitted by the LLM from the prompt**, not
formatted in Python. `build_reference_params` (`seqera/pipeline_params.py:79-109`)
computes `reference_status` from `reference_bundles.json` alone and is blind to
`LURIA_GENOMES`. Because `reference_bundles.json.store_root` is `null`
(`reference_bundles.json:2`), every single-species bundle degrades to
`igenomes_fallback` (`pipeline_params.py:108`), even though `render_run_script`
injects the local `--fasta/--gtf` for any key present in `LURIA_GENOMES`
(`luria/run_script.py:254-260`), regardless of that status.

The two systems agree on the four keys `GRCh38, GRCm39, Mfas6.0, Mmul_10` by a
comment-only contract (`luria/run_script.py:92-93`). We make `LURIA_GENOMES` the
single source of truth.

Changes:
- Expose a predicate `has_local_luria_ref(genome_key) -> bool` from
  `luria/run_script.py` (reading `LURIA_GENOMES`). `seqera/pipeline_params.py`
  imports it, so the dependency points host-resolver -> luria, not the reverse.
- Add a `local_luria` status at the top of `build_reference_params`
  (`pipeline_params.py:87-109`): if the bundle's `igenomes_key` satisfies
  `has_local_luria_ref`, return `({"genome": key}, "local_luria")` before the
  `store_root` / `igenomes_fallback` branches. Priority order becomes
  `local_luria` -> `configured` -> `igenomes_fallback` -> `unconfigured_no_fallback`
  -> `no_bundle`.
- Surface the resolved fasta / gtf filenames in `tool_configure_run`'s JSON
  result (today it returns `resolved_params` / `reference_status` / `bundle_key` /
  `params_yml` / `launch_yml` / `tower_configured`, `agent_tools.py:526-535`; add
  `reference_files`) so the LLM can name them.
- Preserve the existing gencode threading. GENCODE-ness lives in
  `reference_bundles.json.gencode` and is applied at submit time via
  `gencode_for_genome_key` (`pipeline_params.py:63-76`, applied at
  `agent_tools.py:577`). A `local_luria` status must keep threading `gencode`
  from the bundle (human/mouse local GTFs are GENCODE, macaques are Ensembl); it
  must not assume iGenomes.
- Rewrite the messaging in `prompts/pipeline_agent.txt` steps 6-7 (`:16-17`):
  - `local_luria`: "Using the local Luria reference for `<genome>`
    (`<fasta>` / `<gtf>`)."
  - `igenomes_fallback`: keep the existing "no local ref for `<genome>`, using
    the iGenomes key" wording. It is now only reachable when there is genuinely no
    local ref.
  - `unconfigured_no_fallback` and `no_bundle`: unchanged.

No submit-path change is required for reference correctness; the local
`--fasta/--gtf` injection already fires. This thread is a status-enum addition, a
data surface, and a prompt rewrite.

## 8. Thread 5: fetchngs pre-stage on Luria (run.sh / run_script / submitter)

### 8.1 run.sh structure

> **Note (supersedes the shell sketch below):** the inline `python3 - <<'PYIDS'` /
> `<<'PYFILL'` heredocs shown in this subsection are illustrative pseudocode. The
> implementation plan (Tasks 6-7) instead stages a real, unit-tested
> `luria/fetchngs_helpers.py` beside `run.sh` and invokes it as
> `python3 fetchngs_helpers.py ids` / `fill <cache>`. Follow the plan's
> staged-helper contract, not the heredocs.

The template `luria/templates/run.sh.tmpl` gains one new slot,
`{{FETCHNGS_BLOCK}}`, inserted after `cd {{RUN_DIR}}` (`run.sh.tmpl:29`) and before
the unchanged `nextflow run` pipeline block (`:36-44`). When the run has no
SRR-only rows the slot renders empty and `run.sh` is byte-identical to today.

When rendered, the block is (validated interpolations shown as `{{...}}`, all
other text fixed):

```bash
# --- fetchngs pre-stage: fetch SRR-only rows to the shared cache, fill the sheet ---
CACHE="{{FASTQ_CACHE}}"                # <WORKING>/fastq_cache, path-validated
mkdir -p "$CACHE"
# 1. emit ids.csv (accessions for rows with empty fastq_1 and a non-empty
#    accession column) via a fixed, csv-safe helper (no interpolation)
python3 - <<'PYIDS'
# reads samplesheet.csv with the csv module; writes ids.csv (one accession per
# line, deduped) for rows whose fastq_1 is empty and whose accession is set.
PYIDS
if [ -s ids.csv ]; then
  # 2. skip the launch entirely if every accession is already cached
  need=0; while read acc; do ls "$CACHE"/fastq/${acc}*.fastq.gz >/dev/null 2>&1 || need=1; done < ids.csv
  if [ "$need" = "1" ]; then
    # stable, cache-local work dir so -resume reuses downloads across runs
    nextflow run nf-core/fetchngs -r {{FETCHNGS_REVISION}} -profile singularity \
      --input ids.csv --outdir "$CACHE" -w "$CACHE"/work -resume
  fi
  # 3. fixed, csv-safe fill (no interpolation); fails loudly if a fastq is missing
  python3 - "$CACHE" <<'PYFILL'
  # reads samplesheet.csv; for rows with empty fastq_1 and an accession matching
  # ^[A-Za-z0-9]+$, sets fastq_1=<cache>/fastq/<acc>_1.fastq.gz (+ _2 if present,
  # else <acc>.fastq.gz for single-end); exits non-zero if none exist.
  PYFILL
fi
```

Notes:
- `--nf_core_pipeline` is intentionally omitted. We run fetchngs only to populate
  the fastq cache; we build the sample rows ourselves by filename convention, so
  we do not depend on fetchngs' emitted samplesheet. This keeps the design
  pipeline-agnostic and sidesteps fetchngs supporting only a fixed set of
  downstream pipelines (rnaseq is supported, scrnaseq is not).
- fetchngs writes `<CACHE>/fastq/<SRR>_1.fastq.gz` / `_2` for paired and
  `<CACHE>/fastq/<SRR>.fastq.gz` for single-end. The fill constructs those paths.
- The fetchngs run inherits the already-exported `NXF_SYNTAX_PARSER=v1`,
  `NXF_SINGULARITY_CACHEDIR`, and `SSL_CERT_FILE` from the template preamble
  (`run.sh.tmpl:15-27`).

### 8.2 Shared cache

`--outdir <WORKING>/fastq_cache` plus a persistent, cache-local
`-w <WORKING>/fastq_cache/work` plus `-resume`. A per-run work dir would defeat
resume, so the work dir is deliberately stable and shared, not derived from the
run name. Fastqs accumulate under `fastq_cache/fastq/` and persist across
runs; nextflow resume makes overlapping accessions cache hits. The pre-glob in
step 2 avoids launching nextflow at all when every accession is already present.
Caveat: a shared outdir assumes non-concurrent runs per operator; the per-user
`working_path` already scopes it.

### 8.3 render_run_script and submitter

- `render_run_script` (`luria/run_script.py:238-278`) gains kwargs
  `needs_fetch: bool`, `fastq_cache: str`, `fetchngs_revision: str`, and renders
  `{{FETCHNGS_BLOCK}}` as the fragment above or `""`. New fail-closed validators
  mirror the existing ones: `fastq_cache` via a path regex like `_REFS_ROOT_RE`
  (`run_script.py:87`), `fetchngs_revision` via `validate_revision`
  (`run_script.py:61-65`). The fetchngs `nextflow run` is its own invocation with
  an explicit `-r`; it must not go through `resolve_pipeline_source`
  (`run_script.py:225-235`), whose `-r` suppression is only for the vendored
  scrnaseq/star clone.
- `_submit_one` (`luria/submitter.py:95-169`) inspects the staged samplesheet: if
  any row has an empty `fastq_1` and a non-empty `accession`, set
  `needs_fetch=True` and pass `fastq_cache=f"{working}/fastq_cache"` plus the
  fetchngs revision (default `1.12.0`, from the catalog entry
  `seqera/catalog.py:158-173`). No id list is passed from the host; `run.sh`
  derives ids at runtime from the sheet.

## 9. Mixed cohorts

Local and SRR rows share one column schema, so the in-place fill is
column-aligned. A cohort may freely mix reingested-public (SRR) and in-house
(`/net/bmc-*`) samples. The fill only touches rows with empty `fastq_1`; local
rows keep their paths. Our `sample` (leaf UID), `cohort`, and `strandedness` are
preserved for every row, including fetched ones. A fetched fastq that is missing
after the fetch causes a loud non-zero exit rather than a silent drop.

## 10. Error handling and fail-closed posture

- Every new slot entering `run.sh` (`fastq_cache`, `fetchngs_revision`) passes a
  fail-closed regex, matching the existing slot discipline
  (`run_script.py:57-82`).
- Accession values are never interpolated into host-rendered shell. `run.sh`
  derives ids at runtime via a fixed, csv-safe helper over the staged sheet.
- The fill step validates each accession against `^[A-Za-z0-9]+$` before
  constructing a cache path, preventing path traversal from a malformed accession
  even on the Luria side.
- Unresolved fastqs (local path missing, or fetch produced nothing) fail loudly.
- The existing "no resolved genome, defaulting to GRCh38, VERIFY the cohort"
  warning (`submitter.py:123-126`) is preserved.

## 11. Testing

Impacted or new tests (paths under `chat_nextseek/tests/`):
- `test_emitter_launch_split.py` is most impacted. The ENA/Tower assumptions flip:
  `test_emit_nfcore_still_writes_params_and_launch` (`:17`),
  `test_emit_launch_artifacts_alone_writes_yamls` (`:28`),
  `test_emit_launch_does_not_inject_default_genome` (`:43`),
  `test_multi_run_accession_keeps_per_run_ena_urls` (`:103`, the ENA-only fan-out
  invariant, now moot). Keep and extend the local-path tests
  `test_emit_prefers_local_fastq_paths_over_ena_and_writes_lf` (`:57`) and
  `test_fastq_from_meta_picks_path_skips_accession_and_checksum` (`:90`). Add a
  mixed-cohort emitter test (some local rows, some blank SRR rows, tagged not
  dropped) and a path-only-no-accession test.
- `test_pipeline_tool_exposure.py`: `test_existing_static_schema_unchanged`
  (`:79-82`) hardcodes `submit_to_tower` in the static list and must drop it; the
  `_tower_only`/`_luria_only`/`_both`/`_neither` cases (`:16-34`) update to the
  Luria-only default. Keep `61-76` (gencode threading).
- `test_pipeline_params.py`: the `igenomes_fallback` assertions (`:72-75`,
  `:118-125`) split so that a key in `LURIA_GENOMES` now returns `local_luria`,
  while a key not in it still returns `igenomes_fallback`. Add a `local_luria`
  case and confirm gencode still threads.
- `test_pipeline_agent_tools.py`: `test_configure_run_emits_yamls_and_caches`
  (`:300-321`) asserts `reference_status=="igenomes_fallback"` for GRCm39; flip to
  `local_luria`. Same for the override case (`:324-344`).
- `test_pipeline_agent_loop.py`: the canned tool result hardcodes
  `igenomes_fallback` (`:106-125`); update the status and stub assistant text.
- `test_luria_run_script.py`: new cases for the fetch block rendering (present vs
  absent, validators, the `-r` on fetchngs staying separate from the vendored
  clone) and the `has_local_luria_ref` predicate.
- New `test_luria_submitter.py` case: `_submit_one` sets `needs_fetch` when the
  sheet has a blank-fastq SRR row and not otherwise.

Run `uv run pytest tests/ --ignore=tests/evaluator` and, for routing/agent
changes, `uv run e2e.py` per `chat_nextseek/CLAUDE.md`.

## 12. Risks and flagged items

- **Hidden Tower coupling (top structural risk).** Luria launches only work today
  because a full Tower env satisfies `tower_complete`. The redesign must sever
  this or Luria-only deployments silently emit no `launch.yml`.
- **Key-mismatch contract.** `Mfas6.0`-the-bundle equals `Mfas6.0`-the-Luria-key
  only by a comment (`run_script.py:92-93`). The `has_local_luria_ref` predicate
  removes the guesswork; any future rename is caught by the shared predicate, not
  a comment.
- **gencode must keep threading** through the new `local_luria` status; dropping
  it would misformat macaque (Ensembl) or human/mouse (GENCODE) GTFs.
- **Dead `configured` branch (out of scope, flagged).** If `store_root` is ever
  set, `build_reference_params` emits explicit fasta/gtf params but no `genome`
  key, so the Luria submitter would default to GRCh38 (`submitter.py:123`). A
  later cleanup should either remove the `store_root` machinery or have it emit a
  `genome` key too.
- **fetchngs filename convention.** The fill assumes `<SRR>_1.fastq.gz` /
  `<SRR>.fastq.gz`. The executor validates this against a real fetchngs 1.12.0 run
  on Luria before finalizing.
- **Shared-cache concurrency.** Non-concurrent per operator is assumed.

## 13. File-touch summary

Host:
- `seqera/emitter.py`: generalize `_fastq_from_meta` use; blank-and-tag SRR rows;
  Luria-only launch-artifact emit; strip ENA/Tower notes.
- `pipeline/agent_tools.py`: UID-keyed curated paths; drop `resolve_accessions`
  call; drop `submit_to_tower` exposure; surface fasta/gtf in `configure_run`
  JSON; de-mention ENA/Tower in tool descriptions.
- `seqera/pipeline_params.py`: `local_luria` status via `has_local_luria_ref`.
- `config.py`: default launch mode `luria`.
- `prompts/pipeline_agent.txt`: reference messaging (steps 6-7); remove
  ENA/Tower/Seqera mention.

Luria:
- `luria/run_script.py`: `has_local_luria_ref` predicate; `FETCHNGS_BLOCK` render
  + validators.
- `luria/templates/run.sh.tmpl`: the `{{FETCHNGS_BLOCK}}` slot.
- `luria/submitter.py`: `needs_fetch` detection; pass cache + revision.

Left in place, unmentioned: `seqera/ena.py`, `tool_submit_to_tower` and the Tower
client, `prompts/seqera_agent.txt`, the reporter NFCORE note.
