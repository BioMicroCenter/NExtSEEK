# Wave 3 Live Test Access

Wave 3 live testing uses:
- a throwaway MariaDB database auto-created from `DATABASES["seek"]` (or `SPIKE_DB_*` if overridden)
- a throwaway Neo4j database auto-created on the configured Neo4j server

Both throwaway databases are created per-run with unique timestamped names and
dropped on teardown. The test does not read or write the ambient production
Neo4j database.

Safety contract:
- Neo4j DB name is generated inside the test as `wave3-auto-<epoch_ms>` and is
  refused if it ever equals `neo4j`, `system`, or the ambient configured DB.
- MariaDB DB name is generated as `test_w3_<epoch_ms>` and dropped at teardown.
- The test requires the configured Neo4j account to have CREATE/DROP DATABASE
  privilege. Failure to create or drop raises; nothing about the ambient DBs
  changes.

## Required environment

Only one opt-in is required:

```bash
export WAVE3_ALLOW_NEO4J_TEST_DB=1
```

## Optional environment

```bash
# Override MariaDB connection (otherwise defaults to DATABASES["seek"]):
export SPIKE_DB_HOST=...
export SPIKE_DB_USER=...
export SPIKE_DB_PASSWORD=...
export SPIKE_DB_PORT=3306

# Wait timeout for Neo4j DB to reach 'online' status after CREATE (default 30s):
export WAVE3_NEO4J_DB_READY_TIMEOUT_S=60
```

## Preflight check

```bash
/opt/NExtSEEK/.venv/bin/python - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()
from nextseek_api.batch_upload.config import Neo4jConfig
cfg = Neo4jConfig.from_django_settings()
print({"uri": cfg.URI, "user": cfg.NEO4J_USER, "ambient_db": cfg.NEO4J_DB})
PY
```

## Run the live module

```bash
WAVE3_ALLOW_NEO4J_TEST_DB=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /opt/NExtSEEK/.venv/bin/python -m pytest -p pytest_cov --noconftest \
  nextseek_api/batch_upload/tests/test_identity_drift_integration.py -q -rs
```

## Expected skip behavior (no env or partial env)

- Missing MariaDB access in both `DATABASES["seek"]` and `SPIKE_DB_*`: skip
- Missing Neo4j config / URI / credentials: skip
- Missing `WAVE3_ALLOW_NEO4J_TEST_DB=1`: skip

## Cleanup of stale auto DBs

If a prior run crashed between CREATE and DROP, a `wave3-auto-<ts>` Neo4j DB
may be left online. Non-destructive to ambient data; you can drop all stale
auto DBs with:

```cypher
SHOW DATABASES YIELD name
WHERE name STARTS WITH 'wave3-auto-'
RETURN name
```

Then for each name:

```cypher
DROP DATABASE `wave3-auto-<ts>` IF EXISTS
```
