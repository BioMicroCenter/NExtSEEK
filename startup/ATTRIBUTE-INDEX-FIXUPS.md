# Attribute physical-safeguard index fixups

This document is the deep operational reference for the two Rails/SEEK-owned
physical indexes declared in `startup/steps/schema_fixups.py`, which native
attribute mutation requires before it may write to `samples`/`sample_attributes`.
It reports only measured/observed facts; it does not guess values that vary per
install (durations, lock-probe outcomes, row/byte counts).

`startup/README.md` and `startup/CLAUDE.md` are the index for this boundary; this
file is the detail behind their two-line summary. Read the headline below first,
because it is the fact most readers get wrong.

## Headline: the DDL is opt-in and default off

**A stock `./startup.sh install` creates neither index.** Applying them is gated
behind an environment flag that defaults to off:

- The flag is `NEXTSEEK_APPLY_MANAGED_INDEXES`
  (`startup/steps/schema_fixups.py:973`).
- `managed_indexes_enabled()` reads it at call time and returns true only for
  `1`, `true`, `yes` or `on`, case-insensitively, after stripping
  (`startup/steps/schema_fixups.py:992`). An unset variable is false, which is
  the documented default: the docstring states "Opt-in, default off"
  (`startup/steps/schema_fixups.py:976-991`).
- `apply_all()` runs the table and column fixups unconditionally
  (`startup/steps/schema_fixups.py:999-1000`), then checks the flag and, when
  it is off, returns early with one `skipped (set NEXTSEEK_APPLY_MANAGED_INDEXES=1
  to apply)` status per index and never calls `apply_managed_indexes`
  (`startup/steps/schema_fixups.py:1001-1009`). The index DDL is reached only on
  the line past that gate (`startup/steps/schema_fixups.py:1010`).
- The skip is reported by name rather than silently, because "silence here would
  read as applied to anyone scanning install output"
  (`startup/steps/schema_fixups.py:1002-1003`). Note how `install` renders it,
  though: the status line is a warning only for `applied` and
  `constraints reset`, everything else goes through `ui.ok`
  (`startup/cli.py:288-291`), so a skipped index appears as a normal green
  line.

The reason for the gate is recorded in the same docstring
(`startup/steps/schema_fixups.py:979-990`): `MANAGED_INDEXES` targets
`seek_production`, which Rails owns and no Django migration ledger tracks, and
`duplicate_preflight` raises on the first case-variant group, so unconditional
application means a stock install can abort *after* the seeds are loaded and the
containers are up, on real data, with no `--force` and no remediation path. The
opt-in invocation the docstring gives is:

```bash
NEXTSEEK_APPLY_MANAGED_INDEXES=1 ./startup.sh install
```

**Nothing else turns the flag on.** Searched 2026-09-03 with
`/usr/bin/grep -rn "NEXTSEEK_APPLY_MANAGED_INDEXES\|managed_indexes_enabled\|MANAGED_INDEX_FLAG"`
over the worktree excluding `.git/`, `.venv/`, `.superpowers/` and
`node_modules/`: the only hits are six in `startup/steps/schema_fixups.py`
(lines 976, 979, 993, 995, 1004 and 1009, all shown above) and one test that
patches the helper to `False`
(`startup/tests/test_schema_fixups_tables.py:100`). No template renders it into
`docker/nextseek.env`, no compose file sets it, and no test asserts the parsing
of a truthy value.

### Where that leaves a real install

Even with the flag on, `install` is the only command that can apply the indexes.
`apply_all` has exactly one call site in the CLI, `startup/cli.py:288`, inside
the install body; `startup/cli.py:18` is the only other mention of the module in
that file. `reset` reaches it only by re-entering `install`
(`startup/cli.py:497-507`); `rebuild` does not call it at all. Neither the
post-install health checks nor `doctor` looks at an index or a fixup table:
grepping both `startup/steps/doctor.py` and `startup/steps/validate.py` for
`index`, `fixup` and `managed` on 2026-09-03 returns one unrelated `str.index()`
call (`startup/steps/validate.py:93`) and nothing else.

There is no seed fallback either. Measured 2026-09-03,
`zgrep -c "idx_samples_sample_type_id" startup/seed/seek_production.sql.gz` and
the same search for `uq_sample_attributes_sample_type_title_ci` both return 0,
and the seeded `samples` table carries only `idx_samples_uuid`,
`idx_samples_title` and `idx_samples_name_identity`. The ownership marker table
is in neither dump: `zgrep -c 'CREATE TABLE \`nextseek_managed_index_ownership\`'`
returns 0 for both `startup/seed/dmac.sql.gz` and
`startup/seed/seek_production.sql.gz`.

So on a default box the target tables exist (both `samples` and
`sample_attributes` are in `startup/seed/seek_production.sql.gz`, one
`CREATE TABLE` each) and the two safeguards do not. Assume they are absent unless
somebody ran `install` with the flag set on that box, and check with
`attribute_index_readiness()` rather than inferring.

## Owned index names

Both indexes are declared in `MANAGED_INDEXES`
(`startup/steps/schema_fixups.py:373-389`) and can be rebound onto a different
physical database name by `indexes_for_database()`
(`startup/steps/schema_fixups.py:392-396`):

| Table | Index name | Columns | Unique | Preflight |
|---|---|---|---|---|
| `samples` | `idx_samples_sample_type_id` | `sample_type_id` | no | no |
| `sample_attributes` | `uq_sample_attributes_sample_type_title_ci` | `sample_type_id`, `title` | yes | yes, `duplicate_preflight` aborts before any write if a case-variant duplicate `(sample_type_id, LOWER(title))` group already exists (`startup/steps/schema_fixups.py:538-556`) |

The unique index is case-insensitive through the table's collation rather than
through anything in the index definition: `sample_attributes` is declared
`COLLATE=utf8mb4_unicode_ci` in `startup/seed/seek_production.sql.gz`, a `_ci`
collation. On an install whose collation differs, that property does not hold and
the preflight's `LOWER(title)` grouping is stricter than the index it guards.

Ownership is tracked in a durable marker table,
`nextseek_managed_index_ownership` (`startup/steps/schema_fixups.py:330`), one
row per index name, primary keyed on `index_name`
(`startup/steps/schema_fixups.py:407-418`). It stores `shape_sha256`,
`ownership_state` (`create_pending` | `created_by_fixup` |
`preexisting_compatible`) and `owner` (`nextseek-attribute-fixup`,
`startup/steps/schema_fixups.py:329`); the state vocabulary is pinned on the
dataclass at `startup/steps/schema_fixups.py:399-404`. A compatible index that
already existed before the fixup ever ran is marked `preexisting_compatible`
(`startup/steps/schema_fixups.py:774-775`) and is never dropped by reverse; only
an index this fixup itself created (`created_by_fixup`) whose observed physical
shape still matches the managed definition is eligible for reverse
(`startup/steps/schema_fixups.py:843-850`).

## Readiness, apply, reverse, and post-state

The four operations below are what this module executes. Their pinned
real-boundary contract module is `startup/tests/test_schema_fixups.py`
(`startup/tests/test_schema_fixups.py:1-10`), which exercises
`startup.steps.schema_fixups` against a real disposable MySQL/MariaDB database
and never against `seek_production` (`startup/tests/test_schema_fixups.py:5-8`).

- **Readiness** (read-only): `attribute_index_readiness(connection, index)`
  (`startup/steps/schema_fixups.py:520-535`) returns `("table_missing", None)`,
  `("absent", None)`, or `("present", (unique, columns))`. Any
  query/connectivity error propagates unmodified
  (`startup/steps/schema_fixups.py:531-532`); a database that cannot be reached
  is never reported as absent.
- **Apply**: `apply_managed_indexes(repo_root, env)`
  (`startup/steps/schema_fixups.py:936-949`) opens a connection to the `db`
  compose service on its published host port as `root`
  (`startup/steps/schema_fixups.py:912-925`) and delegates to
  `apply_managed_indexes_on_connection(connection, indexes, faults)`
  (`startup/steps/schema_fixups.py:725-744`). That function runs
  `duplicate_preflight()` for every index in the batch *before* any ownership
  write or DDL for any index (`startup/steps/schema_fixups.py:736-737`), so a
  duplicate found on one index aborts the entire batch untouched. Each index's
  `forward_sql()` (`startup/steps/schema_fixups.py:358-364`) is:
  ```sql
  ALTER TABLE `<table>` ADD [UNIQUE] INDEX `<name>` (<columns>), ALGORITHM=INPLACE, LOCK=NONE
  ```
- **Reverse**: `reverse_managed_indexes(repo_root, env)`
  (`startup/steps/schema_fixups.py:958-970`) →
  `reverse_managed_indexes_on_connection(connection, indexes)`
  (`startup/steps/schema_fixups.py:825-870`), which walks the batch in reverse
  declaration order (`startup/steps/schema_fixups.py:835`). It drops only
  indexes whose marker names this owner and state `created_by_fixup`
  (`startup/steps/schema_fixups.py:843-844`) and whose observed shape still
  matches (`startup/steps/schema_fixups.py:846-850`); a compatible preexisting
  index is recorded as `preserved preexisting` and left alone. Each owned
  index's `reverse_sql()` (`startup/steps/schema_fixups.py:366-370`) is:
  ```sql
  ALTER TABLE `<table>` DROP INDEX `<name>`, ALGORITHM=INPLACE, LOCK=NONE
  ```
  Its docstring warns that reverse is a separate, explicit user gate and must
  never be run against a live populated `seek_production` from an automated task
  (`startup/steps/schema_fixups.py:959-961`).
- **Post-state verification**: after DDL, `_verify_final_shape()` re-reads the
  index via `attribute_index_readiness()` and raises `IndexOwnershipError` if
  the physical shape observed after DDL does not exactly match the managed
  definition (`startup/steps/schema_fixups.py:809-814`). DDL that does not
  converge is never silently accepted.

Both `apply_managed_indexes` and `reverse_managed_indexes` accept an optional
`indexes=` override, used by the test lane to rebind onto a disposable database
name (`startup/steps/schema_fixups.py:941`, `startup/steps/schema_fixups.py:962`).
When it is omitted, the frozen `MANAGED_INDEXES` list is used and the connection
is bound to the single database every entry in the batch targets, which is
`seek_production`; a batch spanning two databases is refused rather than guessed
(`startup/steps/schema_fixups.py:873-892`). Reaching either entry point from
`install` still requires the flag in the headline above.

### The schema evidence lane, and why you probably cannot run it

The lane driver is `scripts/attribute_api_test.sh schema`. It is not runnable
outside one developer's machine: it reads its exact test-node selection from
`work/state/attribute-viewset/VERIFICATION-MANIFEST.json` under that developer's
home directory (`scripts/attribute_api_test.sh:138-146`) and writes evidence
under `work/state/attribute-viewset/evidence/...` in the same home directory
(`scripts/attribute_api_test.sh:187`). A grep of `scripts/attribute_api_test.sh`
for that home directory on 2026-09-03 returns 16 lines. With the manifest
absent the schema branch exits 64 with `missing exact schema lane selection`
(`scripts/attribute_api_test.sh:146`).

The disposable MySQL server the lane needs is provisioned by that same script,
which starts a digest-pinned `mysql:8.0` on an `--internal` docker network
(`scripts/attribute_api_test.sh:282-292`); the `disposable_attribute_db` fixture
only attaches to it and asserts it is the same boundary
(`nextseek_api/attributes/tests/attribute_fixtures.py:79-93`). Running
`startup/tests/test_schema_fixups.py` outside the lane fails at import: it
imports `MySQLdb` at module scope (`startup/tests/test_schema_fixups.py:32`),
which the isolated `startup/` project deliberately does not carry. This is why
`startup/CLAUDE.md`'s test command passes
`--ignore=tests/test_schema_fixups.py`.

The manifest document this file previously cited as the source of those
invocations, `task-03-physical-safeguards-job-storage.md`, is not in the
repository. Searched 2026-09-03 with
`find . -name "*task-03*"` and
`/usr/bin/grep -rIl "task-03-physical-safeguards-job-storage" .`, both excluding
`.git/`, `.venv/`, `.superpowers/` and `node_modules/`: the only match anywhere
is this file's own former citation. Section numbers quoted in module docstrings
(11.5, 11.6, 11.7) refer to that unavailable document.

## Captured pre-state and measured metadata-lock observations

Each apply/reverse operation on a single index that gets past the gates produces
one `ddl-telemetry/v1` record (`build_ddl_telemetry_record`,
`startup/steps/schema_fixups.py:632-703`). Two shapes are returned without any
measurement: a table that does not exist yields a three-key stub with verdict
`table missing` (`startup/steps/schema_fixups.py:752-757`), and a skipped
reverse yields the same stub shape (`startup/steps/schema_fixups.py:817-822`).
A full record captures, as measured at the moment of that run rather than
hardcoded:

- `pre_state`: row/byte counts from `INFORMATION_SCHEMA.TABLES`, the ownership
  marker as it stood before this run, and the pre-existing index shape if any,
  with the first indexed column's collation
  (`startup/steps/schema_fixups.py:678-684`, with the collation read by
  `startup/steps/schema_fixups.py:602-614`).
- `observation.duration_seconds`: wall-clock time for the operation on that
  index, measured with `time.monotonic()`
  (`startup/steps/schema_fixups.py:749`, `startup/steps/schema_fixups.py:798`).
- `observation.concurrent_probe`: a `SELECT COUNT(*)` issued after the DDL by
  `observe_concurrent_lock_probe()`
  (`startup/steps/schema_fixups.py:706-722`), reporting `attempted` (always
  `true`), `outcome` (`"succeeded"` or `"errored"`), `blocked_seconds` and
  `server_error_code`. It runs on the same connection, so it evidences that no
  lingering metadata lock from the DDL blocks continued access; a failed probe
  is recorded as evidence, not raised as a fixup failure
  (`startup/steps/schema_fixups.py:708-710`).
- `observation.raw_query_artifact`: **null unless the lane set
  `ATTRIBUTE_EVIDENCE_RUN_ROOT`.** `_raw_query_artifact()` returns `None` when
  that variable is unset (`startup/steps/schema_fixups.py:617-620`), which is
  the case in every ordinary `./startup.sh install`. Inside the lane it writes a
  content-addressed, `chmod 0o444` JSON file under the run's
  `ddl-raw-queries/` directory holding the exact
  `INFORMATION_SCHEMA.STATISTICS` rows observed post-DDL, with its own SHA-256
  recorded in the record (`startup/steps/schema_fixups.py:621-629`).
- `post_state`: final shape SHA-256, statistics rows, ownership marker after the
  run, and `convergence_verdict`, one of `applied`, `already-converged`,
  `recovered`, `reversed`, `preserved preexisting`, `already absent` or
  `table missing` (`startup/steps/schema_fixups.py:693-702`, and the verdict
  assignments at `startup/steps/schema_fixups.py:773`,
  `startup/steps/schema_fixups.py:776`, `startup/steps/schema_fixups.py:778`,
  `startup/steps/schema_fixups.py:791`, `startup/steps/schema_fixups.py:756`,
  `startup/steps/schema_fixups.py:841`, `startup/steps/schema_fixups.py:844`,
  `startup/steps/schema_fixups.py:868`).

### Two `run_identity` fields are unusable off one machine

`base_sha` is read from a hardcoded path in a named developer's home directory,
`work/state/attribute-viewset/VERIFICATION-MANIFEST.json` under it
(`startup/steps/schema_fixups.py:72`). `_base_sha()` returns `None` whenever that
file is absent (`startup/steps/schema_fixups.py:564-566`), so on every other
machine the record's `base_sha` field is null
(`startup/steps/schema_fixups.py:660`) and the record cannot be tied back to a
source revision. Its neighbour `image_id` has the same shape, falling back to a
hardcoded reference digest (`startup/steps/schema_fixups.py:662`,
`startup/steps/schema_fixups.py:71`) that no reader can have built.
`dependency_sha` is likewise null without `ATTRIBUTE_EVIDENCE_RUN_ROOT`
(`startup/steps/schema_fixups.py:571-581`). Both failures are silent: nothing
raises, and the record still validates as `ddl-telemetry/v1`.

Because of the above, this document names no canonical evidence directory. The
only telemetry a reader can obtain is what
`apply_managed_indexes`/`reverse_managed_indexes` return in their own run, and
those callers reduce each record to an `(fqn, convergence_verdict)` pair before
`install` ever sees it (`startup/steps/schema_fixups.py:949`,
`startup/steps/schema_fixups.py:970`,
`startup/steps/schema_fixups.py:952-955`). No duration or lock-probe outcome is
restated here: both are per-run measurements, not fixed properties of the code.

## Absent tables, and `--no-seed` installs

A `--no-seed` install can leave `samples`/`sample_attributes` absent on a fresh
volume. `attribute_index_readiness()` reports this as `table_missing` rather than
`absent`, and `_apply_one_index` returns the stub record and skips the DDL
without writing an ownership row or raising
(`startup/steps/schema_fixups.py:752-757`); it is not a swallowed error. The
marker *table* is still created for that database, because `_ensure_marker_table`
runs before each index is attempted
(`startup/steps/schema_fixups.py:741-743`).

Note the ordering this sits in: `install` calls `apply_all` after the seed phase
(`startup/cli.py:251`) and before `build.start_seek_side`
(`startup/cli.py:310`), and the comment at `startup/cli.py:284-287` states the
intent, that fixups apply whether or not seeds ran so re-installs over a
populated database self-heal.

If the operator later populates the database manually, bypassing the normal seed
step, rerunning `install` is necessary but **not sufficient**: without
`NEXTSEEK_APPLY_MANAGED_INDEXES` set, the rerun reports both indexes as
`skipped` and the two physical safeguards still do not exist. To have them in
place before any native attribute mutation write path reaches
`samples`/`sample_attributes`, the rerun must carry the flag, and readiness
should be confirmed afterwards rather than assumed.

## Known defects in this area (not fixed here)

1. `startup/steps/schema_fixups.py:72` hardcodes a developer home directory, so
   `base_sha` is null in every telemetry record produced anywhere else. Silent;
   zero test failures.
2. `scripts/attribute_api_test.sh` hardcodes the same home in 16 places, which
   makes the documented schema lane unrunnable elsewhere.
3. The `DatabaseError` lookup at `startup/steps/schema_fixups.py:47-56` imports
   from `MySQLdb` unconditionally, unlike the driver lookup at
   `startup/steps/schema_fixups.py:895-909`. On a host with only PyMySQL and the
   flag on, the first database error raised inside the apply or reverse path
   surfaces as an `ImportError` that hides the error it was meant to handle.
