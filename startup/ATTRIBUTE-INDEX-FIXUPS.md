# Attribute physical-safeguard index fixups

This document is the operational reference for the two Rails/SEEK-owned
physical indexes `startup/steps/schema_fixups.py` creates before native
attribute mutation may write to `samples`/`sample_attributes`. It reports
only measured/observed facts; it does not guess values that vary per
install (durations, lock-probe outcomes, row/byte counts).

## Owned index names

Both indexes are declared in `MANAGED_INDEXES` (`startup/steps/schema_fixups.py`)
and are bound to the live `seek_production` database name by
`indexes_for_database()`:

| Table | Index name | Columns | Unique | Preflight |
|---|---|---|---|---|
| `samples` | `idx_samples_sample_type_id` | `sample_type_id` | no | no |
| `sample_attributes` | `uq_sample_attributes_sample_type_title_ci` | `sample_type_id`, `title` | yes (case-insensitive via the table's collation) | yes — `duplicate_preflight` aborts before any write if a case-variant duplicate `(sample_type_id, title)` already exists |

Ownership is tracked in a durable marker table,
`nextseek_managed_index_ownership` (one row per index name, primary keyed on
`index_name`), storing `shape_sha256`, `ownership_state` (`create_pending` |
`created_by_fixup` | `preexisting_compatible`), and `owner`
(`nextseek-attribute-fixup`). A compatible index that already existed before
the fixup ever ran is marked `preexisting_compatible` and is never dropped by
reverse; only an index this fixup itself created (`created_by_fixup`) whose
observed physical shape still matches the managed definition is eligible for
reverse.

## Exact readiness, apply, reverse, and post-state commands

These are the literal manifest-bound invocations (Section 8 of
`task-03-physical-safeguards-job-storage.md`); they run only against the
disposable database provisioned by `lane_boundary.py`, never against
`seek_production`:

```bash
generation_id="$(date --utc +%Y%m%dT%H%M%S.%NZ)-$$"
ATTRIBUTE_EVIDENCE_RUN_ID="${generation_id}-schema" bash scripts/attribute_api_test.sh schema
```

This runs exactly `startup/tests/test_schema_fixups.py`, which exercises the
same production entry points against a real disposable MySQL/MariaDB
database:

- **Readiness** (read-only): `attribute_index_readiness(connection, index)` —
  returns `("table_missing", None)`, `("absent", None)`, or
  `("present", (unique, columns))`. Any query/connectivity error propagates
  unmodified; a database that cannot be reached is never reported as absent.
- **Apply**: `apply_managed_indexes(repo_root, env)` (production entrypoint)
  → `apply_managed_indexes_on_connection(connection, indexes, faults)`. Runs
  `duplicate_preflight()` for every preflight-enabled index across the whole
  batch *before* any ownership write or DDL, so a duplicate found on any one
  index aborts the entire batch untouched. Each index's `forward_sql()` is:
  ```sql
  ALTER TABLE `<table>` ADD [UNIQUE] INDEX `<name>` (<columns>), ALGORITHM=INPLACE, LOCK=NONE
  ```
- **Reverse**: `reverse_managed_indexes(repo_root, env)` (production
  entrypoint) → `reverse_managed_indexes_on_connection(connection, indexes)`.
  Drops only indexes owned with `ownership_state == "created_by_fixup"` whose
  observed shape still matches; a compatible preexisting index is always
  preserved. Each owned index's `reverse_sql()` is:
  ```sql
  ALTER TABLE `<table>` DROP INDEX `<name>`, ALGORITHM=INPLACE, LOCK=NONE
  ```
- **Post-state verification**: after apply, `_verify_final_shape()` re-reads
  the index via `attribute_index_readiness()` and raises `IndexOwnershipError`
  if the physical shape observed after DDL does not exactly match the managed
  definition — DDL that does not converge is never silently accepted.

Both `apply_managed_indexes` and `reverse_managed_indexes` accept an optional
`indexes=` override (used only by the test suite to rebind onto a disposable
database name); the production call from `startup/cli.py` (`apply_all`,
invoked after seed/population and before `start_seek_side`) always passes the
frozen `MANAGED_INDEXES` bound to `seek_production` implicitly via
`_connect_to_managed_database`.

## Captured pre-state and measured metadata-lock observations

Every apply/reverse operation on a single index produces one
`ddl-telemetry/v1` record (`build_ddl_telemetry_record` in
`schema_fixups.py`), persisted as part of the `schema` evidence lane. Each
record captures, as measured at the moment of that run (never hardcoded or
estimated):

- `pre_state`: row/byte counts (`INFORMATION_SCHEMA.TABLES`), the ownership
  marker as it stood before this run, and the pre-existing index shape (if
  any) with its column collation.
- `observation.duration_seconds`: wall-clock time for the apply/reverse
  operation on that index, measured with `time.monotonic()`.
- `observation.concurrent_probe`: an independent `SELECT COUNT(*)` issued
  immediately after the DDL via `observe_concurrent_lock_probe()`, reporting
  `attempted` (always `true`), `outcome` (`"succeeded"` or `"errored"`),
  `blocked_seconds`, and `server_error_code` — this proves no lingering
  metadata lock from the DDL blocks continued access; a failed probe is
  recorded as evidence, not raised as a fixup failure.
- `observation.raw_query_artifact`: a content-addressed, `chmod 0o444` JSON
  file under the run's `ddl-raw-queries/` directory containing the exact
  `INFORMATION_SCHEMA.STATISTICS` rows observed post-DDL, with its own
  SHA-256 recorded in the telemetry record for tamper-evidence.
- `post_state`: final shape SHA-256, statistics rows, ownership marker after
  the run, and `convergence_verdict` (`applied` | `already-converged` |
  `recovered` | `reversed` | `preserved preexisting` | `already absent` |
  `table missing`).

The canonical, currently-measured values for any given install are the
`schema.evidence.json` and its referenced artifacts under
`/home/taishajo/work/state/attribute-viewset/evidence/task-03/schema/<run-id>/`
for the pinned schema-lane run, or the live `ddl-telemetry/v1` records
returned by `apply_managed_indexes`/`reverse_managed_indexes` in a real
`startup` install — this document intentionally does not restate a specific
duration or lock-probe outcome, since both are per-run measurements, not
fixed properties of the code.

## `--no-seed` installs

A `--no-seed` install can leave `samples`/`sample_attributes` absent on a
fresh volume. `attribute_index_readiness()` reports this as `table_missing`
(not `absent`) and `apply_managed_indexes_on_connection` skips DDL for that
index without writing an ownership marker or raising — it is not treated as
a swallowed error. If the operator later populates the database manually
(bypassing the normal seed step), `startup` readiness/apply must be rerun
before SEEK boots, so the two physical safeguards exist before any native
attribute mutation write path can reach `samples`/`sample_attributes`.
