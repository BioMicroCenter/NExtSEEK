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
| `min_api_endpoints_enriched.json`, `min_api_endpoints.json` | REST endpoints, methods, and parameters (enriched has descriptions). Sample search is not among the endpoints you call: every sample question goes to `nextseek-graph`. | Building a `nextseek-parse` plan or an `nextseek-api-read` / `nextseek-api-write` body for a record, people, file or write request. |
| **not a file: run `nextseek-graph-schema`** | The **deployed** graph's schema, read live: node labels, relationships, every sample type with its attributes and their stored values, and the investigation/project/study/assay vocabulary. Pass `--types "TIS,D.SEQ"` for those types' attributes in full. Its `source` field says `catalog` (the live graph) or `fallback` (a committed capture, with the reason) — check it before trusting the answer. | When you need an attribute's exact title or the values it holds to phrase a question, or the user asks what the graph holds. `nextseek-graph` reads the live catalog itself, so this is not a required step before every query. There is no baked graph-schema file to read: a capture goes stale the moment the graph is synced, so ask the graph. |
| `projects_db.json` | This instance's projects and investigations: every row carries `entity_type` (`project` or `investigation`, and the two may share a name), its aliases and its project id; project rows also carry `labs` (each lab's code, name and affiliation). | Resolving a project name to an id (e.g. for `nextseek-report --project`). |
| `read_safe_endpoints.json` | Which endpoints are read-only. | Confirming write-safety classification of an endpoint. |
| `ops.json` | Canonical exported OpSpec list (Plan 005). Not an operation inventory to enumerate by hand; consult it when checking installed shim/export identity. | Confirming the baked operation export matches NExtSEEK OpSpec. |

## Decision shortcuts

- **Any question about samples** (find, filter, count, break down by type / attribute / keyword / assay / project / person, UIDs and lab codes, lineage, which values an attribute holds): `nextseek-graph`. The graph holds every sample attribute as a property, so metadata filters (cell type, treatment, scientist, dates) are graph questions too. The op is held to the user's projects; when it answers through the project-scoped sample search instead, the answer arrives under `fallback` and must be disclosed (the `nextseek` skill says how).
- **A catalog list, people, a file download, one sample's full export, or a write**: `nextseek-parse` → `nextseek-api-read` / `nextseek-api-write`, and only for the endpoints in `read_safe_endpoints.json` (single-record `{uid}` endpoints are refused). `/people/` lists registered users; the person on a sample is its `Scientist` attribute, a graph question.
- **A named cohort/abbreviation** (GBM, CSBC, …): expand it via `capabilities.md` / the auto entity-extract before putting it in a question.
