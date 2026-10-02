# nextseek_api/management/

## What this is

The `manage.py` commands of the `nextseek_api` app. Run any of them in the app container as `docker compose exec nextseek uv run manage.py <command> [options]`. Commands that change data are a dry run unless they say otherwise; read `--help` first.

Four commands here are shims. Django scans only each installed app's own folder for commands, so `dispatch_attribute_outbox`, `recover_attribute_sync_jobs`, `check_attribute_outbox_heartbeat` and `run_assay_registration_jobs` resolve here and call the real code in `nextseek_api/attributes/management/commands/` and `nextseek_api/assay_registration/management/commands/`. The app entrypoint starts the first, second and fourth, `run_share_jobs` and `graph_sync --loop` by name, so deleting a shim removes a command the entrypoint calls.

## Commands (`commands/`)

| Command | What it does |
|---|---|
| `graph_sync` | writes the Neo4j sample graph from MySQL, one mode per run: `--full`, `--catalog`, `--verify` (read-only checks, exit 1 on failure), `--reconcile` (nightly targeted sync), `--drift`, or the drain `--loop` (`nextseek_api/graph_sync/README.md`) |
| `graph_sync_health` | the graph sync health line `./startup.sh rebuild` and `ci` print on every box: stale jobs, failing or dead outbox rows, failed runs, drift; reads only; `--json` |
| `studies` | the studies tool: creates a SEEK study if it is missing and moves samples into it, in SEEK, and the graph follows; `--mode export`, `plan`, `apply`, `graph`, `rollback` and `report` (`nextseek_api/studies/README.md`) |
| `run_share_jobs` | drains the sample-share queue (the studies tool's share mode): plans a share, then runs its link units one SEEK write at a time; loops unless `--once` |
| `dispatch_attribute_outbox` | loops and dispatches pending attribute-change outbox rows to the attribute queue; `--max-iterations N` for a bounded run |
| `recover_attribute_sync_jobs` | recovers synchronous attribute jobs whose web process died; `--loop` repeats, `--check-heartbeat` is the healthcheck |
| `check_attribute_outbox_heartbeat` | exits nonzero unless the outbox dispatcher heartbeat is fresh (a healthcheck) |
| `run_assay_registration_jobs` | claims and runs queued assay-registration jobs; loops unless told to make one pass |
| `cc_sweep_staging` | sweeps a user's completed sidecar staging files into their Container-CC scratch folder; needs `--user-id`, `--api-user`, `--project` |
| `nessie` | runs the Nessie router-aware test harness (`--tier route\|full`); see `NessieAI/tests/README.md` |
| `derive_sample_type_requirements` | derives sample type upload requirements from the sample graph; `--dry-run` prints without writing |
| `backfill_publication_attributes` | backfills DOI and PMID into sample metadata from `--from-studies` or `--from-file`; dry run unless `--apply` |
| `fill_study_publications` | `--extract` writes a review file of DOIs found in study descriptions and touches no database |
| `rename_sample_retrieve_path` | rewrites the old retrieve path in stored Nessie chats; dry run unless `--apply` |
| `scrub_stored_sample_properties` | removes two derived sample properties from stored graph results; dry run unless `--apply`, which needs `--backup-dir` |
