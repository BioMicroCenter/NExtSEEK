# NExtSEEK context files — manifest

These files live in `/app/plugins/nextseek/context/`. They are the agent's ground
truth for NExtSEEK vocabulary, schema, and endpoints. **Consult the file whose
"consult when" matches your task BEFORE constructing an op call** — do not guess
project/study names, sampletype codes, assays, or endpoints from memory.

Note: `nextseek-entity-extract` runs automatically on every query (UserPromptSubmit
hook) and injects resolved vocabulary into your context. These files are the
authoritative source when you need more than the auto-resolution provides.

| File | What it is | Consult when |
|------|-----------|--------------|
| `capabilities.md` | What the assistant can do, and under **Known Projects and Investigations** the generated list of investigations the graph answers to: each exact title, what it studies, the names people use for it, and which ones are not loaded on every instance. How to phrase scoped questions. | **First, for any query** — and always when the query names a project/study/investigation or uses an abbreviation to resolve. |
| `min_sampletypes_db.json` | Canonical sample-type **codes → names** (e.g. MUS=Mouse, NHP=Non-Human Primate, RNA, TIS, PAV, D.SEQ=Sequencing Data). | The query names a kind of sample; map it to its code before parse/graph/api-read. |
| `min_assays_db.json` | Assay/technique **codes → names**. | The query mentions an assay, technique, or data modality. |
| `min_api_endpoints_enriched.json`, `min_api_endpoints.json` | REST endpoints, methods, and parameters (enriched has descriptions). | Building a `nextseek-parse` plan or an `nextseek-api-read` / `nextseek-api-write` body. |
| **not a file: run `nextseek-graph-schema`** | The **deployed** graph's schema, read live: node labels, relationships, every sample type with its attributes and their stored values, and the investigation/project/study/assay vocabulary. Pass `--types "TIS,D.SEQ"` for those types' attributes in full. Its `source` field says `catalog` (the live graph) or `fallback` (a committed capture, with the reason) — check it before trusting the answer. | **Before any `nextseek-graph` query.** There is no baked graph-schema file to read: a capture goes stale the moment the graph is synced, so ask the graph. |
| `min_graph_schema.json` | Hand-written **routing guidance**, not a schema capture: which filters the graph supports — structure/lineage + sampletype + study/investigation title ONLY; cell-type and other metadata fields may need the API instead. | Deciding whether a question is answerable in the graph or needs an API step. |
| `projects_db.json` | This instance's projects and investigations: every row carries `entity_type` (`project` or `investigation`, and the two may share a name), its aliases and its project id; project rows also carry `labs` (each lab's code, name and affiliation). | Resolving a project name to an id (e.g. for `nextseek-report --project`). |
| `read_safe_endpoints.json` | Which endpoints are read-only. | Confirming write-safety classification of an endpoint. |
| `ops.json` | Canonical exported OpSpec list (Plan 005). Not an operation inventory to enumerate by hand; consult it when checking installed shim/export identity. | Confirming the baked operation export matches NExtSEEK OpSpec. |

## Decision shortcuts

- **A query that filters on a metadata field the graph does not hold** (cell type, treatment, RIN, scientist, dates on non-UID fields): resolve those via `nextseek-parse` → `nextseek-api-read` (the API), not `nextseek-graph`. Use the graph only for structure/lineage (`DERIVED_FROM`, `IN_STUDY`, `IN_INVESTIGATION`) and sampletype/title scoping.
- **A named cohort/abbreviation** (GBM, CSBC, …): expand it via `capabilities.md` / the auto entity-extract before using it as a `study`/`investigation`/`project` scope.
