---
name: nextseek-batch-upload
description: >
  Build and validate a read-only NExtSEEK batch-upload workbook for sample create
  or update work. Use when preparing sample metadata rows, resolving upload
  project and assay context, or validating a workbook before human inspection.
---

# NExtSEEK Batch Upload

You prepare a flat create/update workbook and validate it. You do not submit it.
The safe output is the workbook path plus the validation result.

## Run it once, at any size

The flow is identical whether the batch is 1 row or tens of thousands, and you run it
**once over the whole batch** — never once per UID, per UID-prefix, or per "cohort".
A study code embedded in a UID is **not** a project; never split the batch by it.

End-to-end, a create or update batch is three moves:

1. Resolve the target project **once** (Required Flow step 1) and keep its confirmation token.
2. Build **one** rows JSON covering every row in the batch (Required Flow step 5).
3. Run **one** `nextseek-validate-upload` over that whole rows JSON (Required Flow step 7).

That single fused `nextseek-validate-upload` call already fetches the schema, retrieves
the current samples, resolves current assay links, builds the workbook, and validates it
— for **all** rows in one in-process pass. So do **not** run `nextseek-sample-search`,
`nextseek-assay-resolve`, or `nextseek-build-payload` as their own steps, and never loop
them per UID, per SampleType, or per cohort. Each granular op is a fresh call that
re-fetches project and assay context; running them repeatedly is the main cause of slow
or timed-out turns. The numbered steps below are the detail behind those three moves,
plus optional inspection.

## Step 0: Identify the target samples

Before the Required Flow you must know which samples to build rows for. Discover
them with the granular, deterministic ops below — not with `nextseek-query` (available for query workflows, wrong for upload preparation). The
granular ops return a plan and rows you can inspect directly; use them for target
discovery.

- **Curator described the samples by attribute** (e.g. "every TIS sample whose
  Scientist is Smith"): run `nextseek-graph --query "UIDs of <the curator's request
  verbatim>"`. The graph holds every sample attribute, and the op is held to the
  curator's projects; its `result.data` carries each matching sample's UID (under
  `fallback.data.rows` when the op answered through graph_search; the `nextseek` skill
  says what to do when `fallback.ok` is false). Then fetch the current rows of those UIDs with
  `nextseek-sample-search --uid ...` (step 3): the graph is only as fresh as its last
  sync, the database rows are what an update edits. If `nextseek-graph` returns
  `AGENT_FAILED`, retry the identical call once; if it fails again, stop and tell the
  curator. Do not fall back to `nextseek-query`, and do not search through
  `nextseek-parse` + `nextseek-api-read`: the sample-search endpoints are not
  read-safe for the agent, so `api-read` refuses them.
- **Curator already gave explicit UIDs**: skip the search and go straight to
  `nextseek-sample-search --uid <UID> [--uid <UID> ...]`. This op is UID-only; it
  does not search by attribute.

Confirm the discovered UID set with the curator before building anything. Carry
those UIDs into step 3 (retrieve current) and step 5 (rows JSON).

## Required Flow

The create/update path is steps 1, 5, and 7 (see "Run it once, at any size" above).
The fused `nextseek-validate-upload` in step 7 performs steps 3, 4, and 6 for the whole
batch in a single pass — run those granular ops yourself only to inspect inputs, never
in a per-UID, per-SampleType, or per-cohort loop. Step 2 is optional context for
populating rows.

1. Resolve the project with `nextseek-project-resolve`.
   Run `nextseek-project-resolve --name '<project name>' --out <token path>` — it queries the
   live `/projects/` API and resolves the exact title against the accessible projects,
   returning their ids and titles. Resolve by name, not typed id. Get curator-confirmed
   selection before use, then re-run with `--confirmed` to mint the token (when the curator
   already confirmed the named project in their request, run with `--confirmed` directly).
   Never auto-select, even when exactly one project exists, and never derive a project id
   from the cached catalogs under `context/` (for example `projects_db.json`) — they are
   stale snapshots, not authoritative; only this tool's live answer counts. Do not use any
   other nextseek-* tool for project lookup. Keep the non-secret confirmation token and pass
   it to validation.

2. Fetch the structured schema with `nextseek-sampletype-attrs`.
   Do this once per distinct SampleType (a handful — never per UID or cohort), only
   when you need to see the attributes before populating rows; the fused
   `nextseek-validate-upload` fetches the schema itself. Persist the structured
   schema, not just title strings, and use it for value population. If a required value is missing or
   ambiguous, ask for the missing value instead of inventing it. The attrs
   command emits one schema object per SampleType; pass that file (or a JSON
   array of such objects) to `nextseek-build-payload --schema` — the runner
   accepts either shape.

3. Retrieve on update with `nextseek-sample-search`.
   A populated UID means an update. Retrieve the visible current sample row before
   building so the hard gate can preserve current assay links and refuse missing
   or degraded current-assay evidence. Pass the sample-search JSON file directly
   to `nextseek-build-payload --resolved-current` together with `--project-id`.
   Do not invent a `{uid: [assay_id, ...]}` map by hand; the runner resolves
   assay IDs from the search rows without calling advanced_search again.

4. Resolve assay titles with `nextseek-assay-resolve`.
   Use the accessible `/assays/` map and the selected project's assay set as the
   project tie-break. Fail-closed on ambiguity or zero matches for curator-added
   assay titles. Ask the curator instead of guessing.

5. Build the rows JSON.
   Each row has `UID`, `SampleType`, `attributes`, and optional `assay_ids` or
   assay titles. Blank UID creates a sample. Populated UID updates a sample.
   Values belong in `attributes`; the tools build canonical `json_metadata`.

6. Build only staged payloads with `nextseek-build-payload`.
   Use a non-scratch staging directory for build-only inspection. Pass
   `--schema` (attrs output), and on updates also `--resolved-current`
   (sample-search output) plus `--project-id`. Do not hand-edit the sheet. If
   the user changes values, update the rows JSON and rebuild from the source
   rows so schema and gate checks still apply.

   Prefer `nextseek-validate-upload` (fused build+validate) when you do not need
   a staged inspection workbook; it retrieves current assays once in-process.

7. Validate in file mode with `nextseek-validate-upload`.
   Pass `--project-id` and the matching `--project-confirmation` token. The
   validator builds, posts the workbook in file mode, requires
   `structure,name_check,dag`, and then applies the runner hard gate. The hard
   gate checks server verdict, processed row count, structured schema-required
   values, populated update fields, current-assay manifest completeness, and
   delivered-assay superset safety.

8. STOP on failure.
   Any validation failure, hard gate refusal, missing project confirmation,
   schema problem, retrieve problem, assay ambiguity, or promotion failure stops
   the workflow. Fix the source rows or ask the curator, then rebuild and
   validate again.

9. Return the artifact for inspection.
   Report the validation verdict and the generated workbook path. Warn that
   update rows require `update_existing=true` during any later human-run upload,
   because the server default skips populated-UID updates. Flag mixed create+update
   sheets so the curator knows that setting matters.

## Harmonizing values

"Harmonize / normalize / standardize these values" means: map the stored values of one attribute onto the house ontology terms. A mapping the user supplies ("X means Y") is the same task: check each target with the snippet and reply in the same form. Discovery is Step 0 and the write-out is the Required Flow; only the steps below are new.

1. Find the values and their counts, held to the curator's projects: the previous turn's rows when it listed them, else one `nextseek-aggregate` call.
2. Look the attribute up in `/app/plugins/nextseek/context/ONTOLOGIES.json` (sample type + attribute name) and read every ontology file that covers it.
3. Match each value with the snippet below, nothing looser. A value equal to `r["TERM"]` exactly is already harmonized. When several ontologies cover the attribute, take the first that matches. Never map a value the snippet does not match: put your guess as a question.

```python
import json, fnmatch
C = "/app/plugins/nextseek/context/"
IDX = json.load(open(C + "ONTOLOGIES.json"))
def covering(sample_type, attribute):
    key = f"{sample_type}.{attribute}"
    return [e for e in IDX["ontologies"] if any(fnmatch.fnmatchcase(key, p) for p in e["attributes"])]
def match(entry, value):
    norm = (lambda s: " ".join(str(s).split())) if entry["case_sensitive"] else (lambda s: " ".join(str(s).split()).casefold())
    for r in json.load(open(C + entry["file"])):
        if norm(value) in {norm(r["TERM"]), *map(norm, r["SYNONYMS"])}:
            return r
    return None  # no term fits
def link(entry):
    return entry["url"]  # the finished public page, quoted as is in the reply
```

4. Open the reply with one line naming the ontology or ontologies you matched against, each linked: its `title` from `ONTOLOGIES.json` and `link(entry)`, quoted exactly; never write or build an ontology link any other way. The first reply of a harmonization always carries this line and the table, also when it must ask a question first (for example which project): put the question after the table. Then one table, columns Before | Samples | After | Ontology, and the number of samples that would change out of the samples that have a value (the table's rows add up to it). Give the samples with no value as their own number; never call a total that includes them samples with a value. The Ontology cell is the record's `ONT_ID` when it has one; otherwise it is the ontology's `title` and "house list". Never leave it empty or write "none". The house term wins over the most common form; when they differ, say so in one line. Name the values no term fits, each with your guess as a question. Write the per-sample list to `harmonization_proposal.csv` (UID, current value, proposed TERM, ONT_ID). Then ask the user to confirm.
5. No ontology covers the attribute, or no term fits a value: say so, propose the most common form of each group, and ask whether it should become a house term. Write the proposed terms to `ontology_additions.json`, a list of records with the five keys `TERM`, `DEF`, `ONT_SOURCE`, `ONT_ID`, `SYNONYMS`, for the NExtSEEK team to add. Write this file only when new terms or synonyms are proposed. When the user supplies or confirms what an unmatched value means, offer it as a synonym of that term: put that term's record in `ontology_additions.json` with the value added to its `SYNONYMS`, and say the house list will map it without asking once the team adds it. Nothing is added from the chat.
6. After the user confirms, run one `nextseek-validate-upload` per project the affected samples belong to, each after its own project resolve (Required Flow steps 1 and 7), with only that attribute changed: the validator takes one project. When the user did not name the projects, ask which ones first. The user uploads each workbook with `update_existing=true`. Nothing is written to NExtSEEK from the chat.

## Forbidden Actions

POST .../batch-upload/start/ is forbidden. Never submit a batch, trigger write
actions, or treat validation as permission to write. This skill builds and
validates only.

<!-- BEGIN PLAN005-GEN:skill-ops -->
assay-resolve	nextseek-assay-resolve	Resolve assay titles against the selected project.	local_subcommand	read	true	false
attrs	nextseek-sampletype-attrs	Fetch structured sample-type schema.	local_subcommand	read	true	false
build-payload	nextseek-build-payload	Build staged upload payloads from source rows.	local_subcommand	read	true	false
build-validate	nextseek-validate-upload	Fused build and validate of an upload workbook.	local_subcommand	read	true	false
extract	nextseek-extract-text	Extract text from a file.	local_subcommand	read	true	false
project-resolve	nextseek-project-resolve	Resolve a project against the live projects API.	local_subcommand	read	true	false
sample-search	nextseek-sample-search	Retrieve current sample rows by UID.	local_subcommand	read	true	false
<!-- END PLAN005-GEN:skill-ops -->
