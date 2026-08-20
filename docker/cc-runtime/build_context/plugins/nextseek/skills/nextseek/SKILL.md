---
name: nextseek
description: >
  This skill should be used when the user asks to query NExtSEEK — "find/list/show/count
  samples", "retrieve a sample by UID", "show the sample tree / lineage", "run a graph
  query", "refine that search", "what sampletypes/assays exist", "build a project report
  (samples/protocols/published/rppr)", "generate a GEO/SRA/nf-core/PRIDE submission workbook",
  "launch/run/submit an nf-core pipeline (rnaseq/scrnaseq) on the cluster for these samples",
  "plan a multi-step lookup", or "create/update/delete NExtSEEK data". Do NOT trigger on general
  bioinformatics questions, code/file edits, non-NExtSEEK data sources, or file-system tasks.
disable-model-invocation: false
---

# nextseek

Orchestrate the NExtSEEK ops directly. Each op is one stage of the NExtSEEK pipeline, exposed
so the right piece(s) can be invoked for a given question. There is no single do-everything op.
Pick the op(s) a task needs, run them, and compose the answer from what they return. Read this
entire file before taking any action.

Every op runs **server-side** (via the sidecar or the NExtSEEK viewset) and returns JSON on
stdout. The agent container holds only the user's NExtSEEK login (`API_USER`/`API_PASS`) — never
database or provider credentials, and no `chat_nextseek` source. Do not attempt to reach those.

## Context files — read the manifest first

`context/MANIFEST.md` lists every context file with a one-line description and **when to
consult each**. **Read `context/MANIFEST.md` before constructing any op call**, then read the
specific file(s) it points you to. Never guess project/study/investigation names, sampletype
codes, assays, or endpoints from memory — resolve them from these files.

`nextseek-entity-extract` also runs **automatically on every query** (a UserPromptSubmit hook)
and injects resolved NExtSEEK vocabulary into your context before you act. Use those resolved
terms (and the manifest files) — e.g. expand abbreviations like **GBM → the Glioblastoma
investigation** — rather than passing the user's raw phrasing straight to `graph`/`api-read`.

## Tool capability matrix (authoritative contract)

Do not infer capabilities from binary names, repeated `--help` calls, or bin source. This matrix
is the complete contract; there are no hidden flags.

| Tool | Purpose | Input | Output (JSON) |
|---|---|---|---|
| `nextseek-entity-extract` | Resolve NL terms to NExtSEEK vocabulary. | `--query "<text>"` | `{sampletypes, assays, keywords, projects}` |
| `nextseek-parse` | Turn an NL question into a parser plan. | `--query "<text>"` | parser plan `{mode, target_endpoint, filters, ...}` |
| `nextseek-api-read` | Execute a read-safe REST call from a parser plan. | `--parser-plan '<json>'` | API response |
| `nextseek-api-write` | Execute a write (POST/PUT/DELETE) from a parser plan. | `--parser-plan '<json>' --confirmed-write` | API response |
| `nextseek-graph` | Run a Neo4j lineage/graph query from NL. | `--query "<text>"` | `{cypher, result}` |
| `nextseek-report` | Project summary report. | `--mode {samples,protocols,published,rppr} --project <name>` | report `{summary, saved_files, rows}` |
| `nextseek-generate-submission` | Build a submission **workbook** (samplesheet/metadata **file**) for a UID set. Does NOT run/launch a pipeline. | `--type {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE} --uids <csv>` | `{report, type}` |
| `nextseek-pipeline` | **Launch** an nf-core pipeline on the cluster (Luria/Tower) — hand a composed cohort summary to the pipeline agent, which then runs the interactive launch wizard. | `--message "<summary: explicit UIDs + species/genome + metadata + pipeline>"` | `{reply, debug, bundle_id}` |
| `nextseek-plan` | Multi-step planner advisor (read-only). | `--query "<text>"` | `{plan, recommended_next_actions, ...}` |
| `nextseek-query` | Single-shot deterministic NS run in the live chat session; materializes scratch manifest when a bundle is present. | `--query "<text>"` | `{reply, debug, bundle_id}` (+ scratch manifest path when applicable) |
| `nextseek-recall` | Fetch a prior turn's raw rows by `--turn N` from the digest — never re-query for data a prior turn already returned. | `--turn <N>` | `{turn_id, bundle_id, total, row_count, columns, path}` |
| `nextseek-run-ls` | **Reingest step 1** — recursive read-only listing (`ls -laR`) of a finished Luria run directory. | `--run-dir <abs path under the Luria runs root>` | `{tree, truncated, run_dir}` |
| `nextseek-build-upload-xlsx` | **Reingest step 2** — render NExtSEEK 4-sheet upload workbook(s) from composed rows (one per sample type) for the user to review + upload. Does NOT write to NExtSEEK. | `--rows '<json array>' [--existing-parent-uids <csv>]` | `{saved_files, qa}` |

## Choosing the op for a task

**Search / find / list / count / retrieve / sample-tree — parse, then read.** A lookup is two
stages: parse the question into a plan, then execute the plan.

```bash
nextseek-parse --query "Find cell samples with CellType set to T Cell."
# -> parser plan JSON: {"mode": "new_search", "target_endpoint": "...", "filters": {...}}
nextseek-api-read --parser-plan '<the parser plan from the previous step>'
# -> API results; compose the user-facing answer from these
```

`mode` values include `new_search`, `refine_last_search`, `ask_about_last_results`. Refinement
and recall ("which of those…", "what sampletypes were in those results") use the same two-stage
flow — parse the follow-up verbatim; the parser resolves prior context from session state.

**Entity / vocabulary resolution — `nextseek-entity-extract`.** To answer or double-check how a
term maps to NExtSEEK codes (e.g. "CD8 antibodies" → `AB`):

```bash
nextseek-entity-extract --query "Find me all CD8 antibodies in the database."
```

**Inspect a parser plan — `nextseek-parse` standalone.** To show or verify the plan (mode,
endpoint, filters) for a question without executing it:

```bash
nextseek-parse --query "Find bacteria samples with strain mTB."
```

**Lineage / relationships — `nextseek-graph`.** Multi-hop traversals in the Neo4j graph:

```bash
nextseek-graph --query "Show me all NHPs in the SRP project."
```

**Project summary report — `nextseek-report`.** When a project (and, if stated, a mode) is named:

```bash
nextseek-report --mode protocols --project "CGR"
```

Derive the mode from the phrasing (`samples`, `protocols`, `published`, `rppr`); default to
`samples`.

**Submission workbook — `nextseek-generate-submission`.** Builds a GEO / SRA / nf-core / PRIDE
**workbook** (a samplesheet/metadata *file*) for a UID set. It does NOT run anything:

```bash
nextseek-generate-submission --type SRA --uids "D.SEQ-230512FOR-288-PUB,D.SEQ-230512FOR-289-PUB"
```

Map the phrasing to `--type` ("nf-core rnaseq" → `NFCORE_RNASEQ`) and read `--uids` from the
sample IDs named.

**Pipeline launch — `nextseek-pipeline`.** When the user wants to **run / launch / submit a
pipeline** on the cluster (Luria/Tower) for samples you've already resolved — not merely produce a
workbook — compose ONE comprehensive summary of the chat and hand it to the pipeline agent:

```bash
nextseek-pipeline --message "Launch the nf-core scRNA-seq pipeline on these 6 NExtSEEK Sequencing Data samples (species: rhesus macaque; study: Gideon 4wk): D.SEQ-220823SHA-1, -2, -3, -4, -5, -6. Resolve them, then propose genome + params before launching."
```

Do your best to summarize everything relevant: the **explicit sample UIDs**, the **species/genome**,
any pertinent **metadata/provenance**, and the **nf-core pipeline** the user asked for. The pipeline
agent reasons over your message (it picks the pipeline, resolves the cohort, and proposes
genome/params), so include what it needs. After you call this op, relay its reply — the wizard's
real first proposal — and let the user confirm in chat; **those follow-up turns continue on the
NExtSEEK side, not here.** **Decision rule:** intent is to *run/launch/submit/execute* a pipeline →
`nextseek-pipeline`; intent is to *build/generate a submission or samplesheet file* →
`nextseek-generate-submission`. **On a `nextseek-pipeline` error, do NOT fall back to
`nextseek-generate-submission`** — report the error and let the user retry.

**Reingest pipeline outputs — `nextseek-run-ls` + `nextseek-build-upload-xlsx`.** After an nf-core
run finishes on Luria, register its outputs as new NExtSEEK analysis samples. This produces an
upload sheet for the user to REVIEW and upload — it does **not** write to NExtSEEK. Workflow:

1. `nextseek-run-ls --run-dir <finished run dir>` → the recursive `ls -laR` tree of the outputs.
2. Reason over the tree + the sample-type catalog. Decide, per output, which `A.*` analysis type it
   is (BAM → `A.ALN`; count/expression matrix → `A.SCXP`/`A.GEX`; VCF → `A.VCF`). Get the input
   cohort's `Scientist`, project, and how existing `A.*` rows cite `Parent` with **`nextseek-api-read`
   on a `D.SEQ` sample** — these are sample *attributes*, so use `api-read` (a REST fetch), NOT
   `nextseek-graph`. Graph is for lineage traversal only; asked for metadata it returns empty Cypher.
   Only if a value genuinely can't be fetched, mark it `*** PLACEHOLDER ***` — do not block on it.
3. Compose one row per output sample: `{"SampleType": "A.SCXP", "json_metadata": {"Parent": "<input
   D.SEQ UID>", "Scientist": "<carried from the input D.SEQ>", "Pipeline": "...", "ReferenceGenome":
   "...", "Aligner": "...", "File_PrimaryData": "...", ...}, "assay_ids": [<int>...]}`. `Parent` is the
   input `D.SEQ` UID(s) the output derives from (`;`-delimited for a merged/aggregate output). Use
   `*** PLACEHOLDER: <what> ***` for any required value you cannot derive — never leave it blank.
4. `nextseek-build-upload-xlsx --rows '<json array>' --existing-parent-uids "<input D.SEQ UIDs, csv>"`
   → renders one 4-sheet workbook per sample type as a downloadable artifact, with a per-type QA
   verdict `{disposition, hard, soft}`. Relay the workbook(s) + QA to the user. If QA HARD_REJECTs a
   type, fix the flagged rows and re-run.

The user reviews the workbook(s) and uploads them via the normal batch-upload UI — **you do not
upload**; producing the reviewable sheet is the final step.

**Multi-step "do X, then Y" — `nextseek-plan`.** See the planner section below.

**Create / update / delete — parse, confirm, then write.** Any create/update/delete is a WRITE.
Build the body by parsing the instruction, apply the Layer-3 confirmation, then write:

```bash
nextseek-parse --query "Create Investigation 'Testing 404'"     # build the request body
# ... Layer-3 plain-text confirmation; wait for the user's "yes" ...
nextseek-api-write --parser-plan '<plan>' --confirmed-write
```

**Pure capability / vocabulary questions — read the cached catalogs.** For "what sampletypes
exist?", "what can I ask?", read the baked catalogs directly with `Read` (no op, no network):
`/app/plugins/nextseek/context/capabilities.md` (start here), `min_sampletypes_db.json`,
`min_assays_db.json`, `min_api_endpoints_enriched.json`, `projects_db.json`, `neo4j_schema.json`.
For *data* questions, use the ops above — the catalogs alone will not answer those.

## Multi-step planner (`nextseek-plan`)

Use `nextseek-plan` for a single compound request whose second step depends on the first's
results — "do X, **then** do Y on those". Signals: a sequencing conjunction ("then", "and then",
"after that", "based on those results, …") joining two dependent asks.

```bash
nextseek-plan --query "Find me mouse samples in the Kamm project, then filter those to only female animals."
```

`nextseek-plan` is read-only: it executes the read-safe steps and returns recommended actions. If
the plan advises a write, stop and route that write through `nextseek-api-write` under Layer 3 —
the planner never writes. For a single non-compound lookup, use `nextseek-parse` → `nextseek-api-read`.

## Composing the reply

Compose the user-facing answer from each op's JSON output.

- Surface what the user asked for, not raw JSON (unless the user says "show me the parser plan"
  / "show me the API response").
- Do not fabricate counts, UIDs, or fields, or fill in numbers from prior knowledge — report only
  what the op returned. State an empty result plainly.
- Quote the **host-side path** of any artifact produced (submission workbook, report, file under
  `/data/scratch/`). Read `DMAC_PATH_MAPPINGS` from the env to translate container paths to host
  paths. If it is absent or unparseable, report the container path and note the mapping was
  unavailable.

## Write safety — 3 layers

For non-GET operations (`nextseek-api-write`, write-class endpoints):

- **Layer 1 (mechanical, deployment-dependent)**: a Claude Code permission allowlist / deny rule that gates `nextseek-api-write`. **In the dmac-assistant bridge POC, the `container_cc` route runs under `--permission-mode auto` (per the host bridge's launch command), NOT `--dangerously-skip-permissions`.** Under auto mode, blanket `Bash(*)` allow rules are dropped and every tool call — including `nextseek-api-write` — is screened by the auto-mode classifier, which blocks escalation/exfiltration. That classifier is a behavioral gate, not a hard guarantee, and no explicit `Bash(nextseek-api-write:*)` deny rule is shipped here. Treat L1 as defense-in-depth, not as a guarantee — the load-bearing layers are L2 and L3.
- **Layer 2 (mechanical, always on — enforced server-side)**: an `api-write` op is refused unless write confirmation is explicit. The `nextseek-api-write` shim requires `--confirmed-write`, and the authoritative gate now runs **outside** the agent container: the sidecar's write gate (`sidecar/app/write_gate.py`) refuses the op unless `confirmed_write` is exactly `True`, and NExtSEEK enforces its own server-side write gate behind that. Because neither gate runs in a process the in-container agent controls, the agent cannot bypass L2.
- **Layer 3 (behavioral, this skill — load-bearing)**: NEVER call `AskUserQuestion` (`container/CLAUDE.md` forbids it; the chat UI doesn't render the widget). Instead, write plain text:

> "About to execute a WRITE-classified operation. Method: POST. Endpoint: /samples/<...>/. Body: {...}. **Confirm?**"

Then wait for the user's next message. If the user responds "yes" / "go ahead" / similar, invoke `nextseek-api-write` with `--confirmed-write`. If anything else, abort and acknowledge.

## Stop-after-2 rule (load-bearing)

This rule applies to **every** `nextseek-*` tool. If a `nextseek-*` tool returns an unsupported answer, empty/null fields that look wrong for the question, or a non-zero exit, you MAY retry **once** with a corrected invocation — rephrase the question, fix a typo'd literal, correct a wrong `--type` / `--uids` / `--mode` value, or supply a missing precursor step (e.g. a `nextseek-parse` plan before `nextseek-api-read`). **Do NOT make a third attempt, and do NOT switch to a different `nextseek-*` tool to "preflight" or reverse-engineer the failure.**

If the second attempt also fails, STOP and reply to the user in plain text with:

- What was attempted (the two calls you made, including arguments)
- The error / unexpected output you observed
- One specific clarifying question that would unblock you (e.g. "Did you mean sample type X or Y?", "Are these UIDs published?", "Which project should I scope this to?")

The dmac-assistant chat UI does not render `AskUserQuestion`, so the clarification MUST be plain text. This is a hard cap: two attempts per user question across all `nextseek-*` tools combined, then a plain-text clarification ask.

### Hard prohibitions after a failed nextseek-* call

After a `nextseek-*` tool returns nulls, empty data, or a non-zero exit, you MUST NOT do any of the following — these are budget-sinks that cannot produce a correct answer:

- `Read` any file under `/app/plugins/nextseek/bin/` — those are the runner internals, not user-facing docs
- `Grep` or `Glob` `/app/plugins/nextseek/bin/` for keywords (`dry_run`, `report_writer`, `submission`, etc.) — the `chat_nextseek` source is NOT present in this image; there is nothing to find
- run `python3 -c "import inspect; inspect.getsource(...)"` against any `chat_nextseek.*` symbol — it is not importable here
- call `--help` repeatedly looking for hidden flags — the matrix above is the complete contract; there are no hidden flags
- call a sibling `nextseek-*` tool to attempt to "fetch what the failed tool needed"

The only legitimate chaining is the documented recipes above (`nextseek-parse` → `nextseek-api-read`, `nextseek-parse` → `nextseek-api-write`); do not invent others.

## Errors

The runner emits a one-line JSON error to stderr with a code (exit code in parens):

- `CONFIG_MISSING` (2): `API_USER`/`API_PASS` not set. Tell the user; do not retry.
- `IMPORT_FAILED` (2): a required module is unavailable server-side. Surface a deploy-side message.
- `VALIDATION` (3): bad CLI args. Fix the call.
- `AGENT_FAILED` (4): LLM/network failure. Retry once with the same call; if still failing,
  surface the structured payload to the user.
- `WRITE_BLOCKED` (5): write shim without `--confirmed-write`, or `nextseek-api-read` received a
  non-read-safe endpoint. Apply the L3 prompt only for true writes; otherwise fix routing.
- `CONFIG_ERROR` (6): a plugin/config file is missing server-side. Deploy-side issue; surface as
  "plugin misconfiguration, please rebuild image."
- `TRANSPORT_ERROR` (7): sidecar/viewset unreachable. Surface as a deploy-side issue.
- `AUTH_FAILED` (8): NExtSEEK rejected the login. Tell the user to check credentials.
- `STAGING_ERROR` (9): artifact staging failed server-side. Surface the message.

<!-- BEGIN PLAN005-GEN:skill-ops -->
api-read	nextseek-api-read	Execute a read-safe REST call from a parser plan.	sidecar	read	true	true
api-write	nextseek-api-write	Execute a write (POST/PUT/DELETE) from a parser plan.	sidecar	write_confirm	true	true
build-upload-xlsx	nextseek-build-upload-xlsx	**Reingest step 2** — render NExtSEEK 4-sheet upload workbook(s) from composed rows (one per sample type) for the user to review + upload. Does NOT write to NExtSEEK.	sidecar	read	true	true
entity	nextseek-entity-extract	Resolve NL terms to NExtSEEK vocabulary.	sidecar	read	true	true
generate-submission	nextseek-generate-submission	Build a submission **workbook** (samplesheet/metadata **file**) for a UID set. Does NOT run/launch a pipeline.	sidecar	read	true	true
graph	nextseek-graph	Run a Neo4j lineage/graph query from NL.	sidecar	read	true	true
parse	nextseek-parse	Turn an NL question into a parser plan.	sidecar	read	true	true
pipeline	nextseek-pipeline	**Launch** an nf-core pipeline on the cluster (Luria/Tower) — hand a composed cohort summary to the pipeline agent, which then runs the interactive launch wizard.	viewset	unrouted	true	true
plan	nextseek-plan	Multi-step planner advisor (read-only).	viewset	unrouted	true	true
query	nextseek-query	Single-shot deterministic NS run in the live chat session; materializes scratch manifest when a bundle is present.	viewset	unrouted	true	false
recall	nextseek-recall	Fetch a prior turn's raw rows by `--turn N` from the digest — never re-query for data a prior turn already returned.	viewset	unrouted	true	false
report	nextseek-report	Project summary report.	sidecar	read	true	true
run-ls	nextseek-run-ls	**Reingest step 1** — recursive read-only listing (`ls -laR`) of a finished Luria run directory.	sidecar	read	true	true
<!-- END PLAN005-GEN:skill-ops -->
