# context/

Hand-owned source of truth for NExtSEEK's catalog context. Edit these files; do not edit the
database tables or the exported JSON directly. Whatever is in these files is what the
database will hold.

`scripts/context_gen.py` is the generator that gets them there:

```
python scripts/context_gen.py --emit update --table all --out /tmp/context.sql
python scripts/context_gen.py --emit seed --table all
python scripts/context_gen.py --emit exports --table all
python scripts/context_gen.py --emit capabilities --counts /tmp/counts-local.json
```

`--emit update` writes re-runnable SQL for a live database; the operator applies it.
`--emit seed` rewrites the held `startup/seed/sql/*_context.curated.sql` files, which no
install step reads until the content is signed off (`scripts/README.md` group C).
`--emit exports` rewrites Nessie's committed JSON exports in
`NessieAI/chat_nextseek/src/chat_nextseek/context/`. Those have two readers and only one of
them sees a database: `_fetch_context_files_from_db` rewrites them from these tables once
per UTC day inside the **app**, so editing one by hand changes nothing that survives a day,
but the **cc-agent** image bakes three of them out of the checkout at build time
(`startup/lib/layout.py::CANONICAL_CONTEXT_FILES`) and has no refresh path, so the committed
bytes are what Container-CC reads for the life of the image. Run `--emit exports` in the same
change as `--emit update`, or the database and the image disagree and nothing reports it: the
stack-health check `cc-agent context` compares the checkout with the image, so two stale
copies of one file read as green. `--emit capabilities` writes the investigation list in
`NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md` from the investigation
rows of `projects.json`. `scripts/README.md` group C is the generator's
reference and `NessieAI/tests/api/test_context_gen.py` is its test lane.

**Review gate:** nothing from these files is written to any database (local, fairdata-dev or
production) until the user has reviewed them as an xlsx workbook and signed off.

| file | feeds | one row per |
|---|---|---|
| `sample_types.json` | `dmac.sample_types_context` | sample type (`sample_type` code) |
| `assays.json` | `dmac.assay_context` | internal assay (`internal_assay_id`) |
| `assay_mappings.json` | `dmac.internal_assays` + `dmac.assays_internal_assays` | SEEK assay that needs an internal assay |
| `projects.json` | `dmac.projects_context` | project or investigation (`name`, `entity_type`) |

## Conventions

- Keys are the database column names, spelled exactly (`Tags` is capitalised on both
  sample types and assays). The autoincrement `id` is left out; the generator owns it. A key
  that is not a column of its table fails at generation, so a typo here never reaches a
  write. The one exception is `present_on` in `projects.json` (below), which the generator
  reads and never writes to a database.
- Sample types sort by `sample_type`, assays by `assay_name`, projects by `name` case-folded,
  a project row before an investigation row of the same name.
- Written with `json.dumps(rows, indent=2, ensure_ascii=False)` plus a trailing newline.
- List-like text columns (`Tags`, metadata fields, parents, children, associated assays) stay
  comma-separated strings. The website and Nessie split them on commas.
- `projects.json` `alternative_names` and `key_data_types` are real JSON arrays here; the
  database stores them as JSON text under CHECK constraints. `project_id` is always a SEEK
  project id (the website and Nessie read it that way). Alternative names are what users
  type for the program (Nessie matches them as substrings, so short or common words such as
  "RMS", "BMC" or "White" go in `tags`); `key_data_types` lists only data the project holds.
- `pi` is display prose: the project page shows it and the entity agent reads it as context.
  Nothing parses it. Lab codes and lab heads' surnames come from SEEK's institution titles,
  never from this file.
- A `projects.json` row is keyed on `(name, entity_type)`, and `entity_type` is exactly
  `project` or `investigation`: a project and an investigation may share a name (CSBC and
  MetNet do). An alternative name may not, once folded (case, accents, surrounding space),
  equal another row's name or alternative name, with one exception: an investigation row
  may repeat its own parent project's name or aliases. That is what bridges "Impact" to
  `Impactb Investigation`, and it is why an investigation's exact title is never a project
  row's alias.

## Investigation rows (`projects.json`)

An investigation row stands for the SEEK investigation that holds the samples, and its rows
are what the investigation list in `capabilities.md` is generated from
(`scripts/context_gen.py --emit capabilities`).

| key | rule |
|---|---|
| `entity_type` | `investigation` |
| `name` | the exact SEEK investigation title that holds the samples, byte for byte, never a paper-tracking copy's |
| `project_id` | the owning project's SEEK id, which equals the parent project row's `project_id`; `null` only when that id differs by instance, which requires `present_on` |
| `parent_project` | the owning project row's `name`; with no such row, the owning SEEK project's title. Required: it is what makes the row an investigation to every reader (`chat_nextseek.context_rows`), because production's table, until the 6.16 write, types its project rows `investigation` with none |
| `alternative_names` | what users type for it; may repeat the parent project's name or aliases |
| `present_on` | generator-only, below |
| `research_focus` | required, one line, at most 200 characters, no count; it becomes the bullet |
| `description`, `tags` | curated prose; keep them short, since every row reaches the entity agent on every turn |
| `pi`, `key_data_types`, links | `null` / `[]`: they belong to the project row |

`present_on` says which instances hold the investigation. Absent or `null` means every
instance. Otherwise it is a non-empty list drawn from `local`, `dev` and `prod` (the
`--ci-profile` vocabulary), each once and not all three, and only an investigation row may
carry it. It renders `(not on every instance: loaded on local and dev only)` after the
bullet, and the generator refuses a counts file that contradicts it. It is never written to
a database: its readers are served by the generated list.
- Field names in metadata columns must match the SEEK attribute titles of that type exactly.
- The three metadata lists together name every SEEK attribute of the type, each exactly once:
  `required_metadata`, then `standard_metadata` (collected routinely), then
  `possible_metadata_fields` (everything else).
- `required_metadata` is a house convention, stricter than SEEK. SEEK requires only UID on most
  types; the house also asks for Scientist, the data file fields on data types, and Name on the
  non-data types that have one (most data types leave Name optional). Every attribute SEEK
  marks required is in it.
- `associated_assay_parents` / `associated_assay_children` must use names exactly as they
  appear in `assays.json` `assay_name`; the website links by slugifying them.

## `repository_attributes` (sample types)

`null` when a type is never deposited in a public repository. Otherwise an object keyed by
repository (`"GEO"`, `"SRA"`, `"PRIDE"`). Each value maps that repository's field name to one of
**the row's own fields** as `"TYPE::Field"`, where `TYPE` is always the row's own code: a row
never maps another type's field. A value may be a JSON array of the row's own fields in order,
meaning the first non-empty one wins (D.MSP `file_path`: the path, else the file name).

Twenty-two types are deposited, each with its own-field mapping:

| repository | types |
|---|---|
| GEO | D.SEQ, A.GEX, A.SCXP, A.CHRM, A.SPTX |
| SRA | D.SEQ, A.ALN |
| PRIDE | D.MSP, A.MSP, A.PHP, A.IMP |
| ImmPort | D.FLOW, D.FCS, D.CYTOF, A.FLOW, A.CYTOF, D.LMX, D.TITR, D.FCRB |
| BioImage Archive | D.IMG |
| METASPACE | D.MSI |
| EVA | A.VCF |
| CCDC | D.CRY |

Repository keys are exactly these names. Repository fields that live on
other types (GEO organism, the SRA BioSample sheet, PRIDE species and tissue) are not mapped;
the repository fields each row still lacks, with the template each mapping follows, are listed in
`CONTEXT_FILES/review_pack_2026-09-15/sample_types/repository_attributes_proposal_v2.json` (GEO,
SRA, PRIDE) and `repository_attributes_other_repos_proposal.json` (the other five).
(Decided 2026-09-15; it replaces the 2026-09-14 ancestor-field rule.)

## `assay_mappings.json`

A flat list of operations on `dmac.internal_assays` and `dmac.assays_internal_assays`. The
generator applies them grouped in this order: renames, creates, maps and remaps, merges.

| `action` | keys | meaning |
|---|---|---|
| `rename_internal` | `internal_assay_id`, `from_title`, `internal_assay_title` | retitle an internal assay |
| `create_internal` | `internal_assay_title` | new internal assay; AUTO_INCREMENT assigns its id and the emitted SQL copies it back into the matching `assay_context` row, whose `internal_assay_id` is `null` here |
| `map` | `seek_assay_id`, `seek_title`, `internal_assay_title` | a SEEK assay with no internal assay (NULL today) gets one |
| `remap` | `seek_assay_id`, `seek_title`, `from_internal_assay_id`, `internal_assay_title` | a SEEK assay moves to another internal assay |
| `merge_internal` | `internal_assay_id`, `from_title`, `into_internal_assay_title` | delete an internal assay whose SEEK assays were all remapped into the survivor |

Targets are named by title, after renames. `from_*` keys are the production values, so the
generator can refuse if production has moved. A row in `assays.json` with
`internal_assay_id: null` is a new internal assay: its `assay_name` must equal the title of a
`create_internal` entry. Every rename and merge is also a graph relabel (TASKS.md D-1).

## Provenance

The first commit is an exact copy of production's rows as pulled on 2026-09-11
(`sample_types_context` 101 rows, `assay_context` 217 rows, `projects_context` 10 rows).
