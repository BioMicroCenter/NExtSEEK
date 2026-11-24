# `nextseek_api/definitions_sync/`

## What this is

`manage.py definitions_sync`: production's definitions copied onto another box (dev, a local stack), so the test
bed starts from production's text. After launch, definitions are edited on production; dev and local copy them.

| file | does |
|---|---|
| `dump.py` | every definition table of one box as one JSON bundle, keyed by code or title (never by id: ids differ per box). Standard library plus mysql-connector, so it also runs piped into a container whose image lacks this package (production) |
| `sync.py` | `plan(prod, dev, hold)`: the actions and every difference left on purpose. Pure: no database, no Django |
| `apply.py` | the plan's writes, each through the path that already writes it |
| `../management/commands/definitions_sync.py` | the command: dry run unless `--apply`; its docstring has the options and exit codes |

The operator runbook (read production, dry run, post the new SEEK types, apply, check) is the repo skill
`.claude/skills/definitions-sync/SKILL.md`.

## What is copied, recomputed and left

| | what | how |
|---|---|---|
| copied | attributes of every sample type both boxes have: missing ones created at production's place, then description, required, value type, unit, vocabulary, linked type, title flag; the order of shared attributes | the attribute API (`nextseek_api/attributes/`), as the operator: one patch request (no sample rewritten), then one create request (each type's samples rewritten once, a job past the API's threshold) |
| copied | the hand columns of `sample_types_context` and `assay_context`, and their missing rows | `services/definitions_admin.py` (the Definitions page's functions and rules) |
| copied | `internal_assays` titles, `sample_types_clades` rows, `sample_attributes_unique` meanings | the Assay map's and Clades page's table helpers; SQL for meanings (`ci/writers.py` WR-38) |
| created by the operator | a SEEK sample type the box lacks | `POST /nextseek_api/sample_types/` with the body the dry run prints: SEEK keeps a permission lookup per type that only its own create fills. The next run adds the type's other attributes, clade and context row |
| recomputed | ids (`sampletype_id`, `internal_assay_id`), the derived context columns (clade, possible fields, the four connection lists), the SEEK-assay map | graph_sync's context step (`graph_sync/context_derived.py`) and each box's own data |
| left, listed | rows only this box has; SEEK's own columns (template index, accessor name, uploaded-template flag, PID, ISA tag); vocabularies and terms (no write path); titles that differ only in case (this box's spelling kept) | |
| left, listed | the hold list: cells that keep this box's value because it is newer work (`{"table", "key", "column"}`, `column` `*` for the whole row) | given with the source |

Then `catalog`, `assay_map` and `context` are queued once, so the graph follows; the attribute API queues its own.

## Rules

- Never a write on production: the command runs on the box it copies onto, and production is only read by `dump.py`.
- Nothing is deleted. A row production lacks is listed under `expected.dev_only`.
- Re-runnable: a second `--apply` finds no actions (`test_applying_the_plan_in_memory_leaves_nothing_to_do`), and
  the dry run after an apply is the proof: its `expected` section is every difference that is left, by reason.

## Tests

`nextseek_api/tests/test_definitions_sync.py`, synthetic bundles only (this repository is public), in the
throwaway container (`ci/README.md` "Running and testing").
