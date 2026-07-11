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
them with the granular, deterministic ops below — not with `nextseek-query`. The
granular ops return a plan and rows you can inspect directly; use them for target
discovery.

- **Curator described the samples by attribute** (e.g. "every TIS sample whose
  Scientist is Owen Leddy"): run `nextseek-parse --query "<the curator's request
  verbatim>"` to turn it into an `advanced_search` plan, then
  `nextseek-api-read --parser-plan "<plan JSON>" --query "<same request>"` to
  execute it. The response rows carry each matching sample's UID and metadata.
  If `nextseek-parse` returns `AGENT_FAILED`, retry the identical call once; if it
  fails again, stop and tell the curator. Do not fall back to `nextseek-query`.
- **Curator already gave explicit UIDs**: skip parse/api-read and go straight to
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

## Forbidden Actions

POST .../batch-upload/start/ is forbidden. Never submit a batch, trigger write
actions, or treat validation as permission to write. This skill builds and
validates only.
