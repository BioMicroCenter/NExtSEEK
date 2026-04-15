# Wave 3 Live Test Access

Wave 3 live testing uses:
- a throwaway MariaDB database created from `SPIKE_DB_*`
- a dedicated Neo4j test database selected by `WAVE3_NEO4J_TEST_DB`

Safety rules:
- never point `WAVE3_NEO4J_TEST_DB` at the normal configured graph database
- never use `neo4j` or `system`
- use a dedicated name starting with `wave3_` or `test_`
- set `WAVE3_ALLOW_NEO4J_TEST_DB=1` explicitly before running

Required environment:

```bash
export SPIKE_DB_HOST=...
export SPIKE_DB_USER=...
export SPIKE_DB_PASSWORD=...
export SPIKE_DB_PORT=3306
export WAVE3_NEO4J_TEST_DB=wave3_identity_drift_test
export WAVE3_ALLOW_NEO4J_TEST_DB=1
```

The Django Neo4j settings must still provide the real server URI and auth. The
test overrides only the database name, not the server credentials.

Recommended preflight:

```bash
python - <<'PY'
from nextseek_api.batch_upload.config import Neo4jConfig
cfg = Neo4jConfig.from_django_settings()
print({"uri": cfg.URI, "ambient_db": cfg.NEO4J_DB})
PY
echo "$WAVE3_NEO4J_TEST_DB"
```

Run the live module:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /opt/NExtSEEK/.venv/bin/python -m pytest -p pytest_cov --noconftest nextseek_api/batch_upload/tests/test_identity_drift_integration.py -q -rs
```

Expected safety behavior:
- missing MariaDB env vars: skip
- missing Neo4j config: skip
- missing `WAVE3_NEO4J_TEST_DB`: skip
- missing `WAVE3_ALLOW_NEO4J_TEST_DB=1`: skip
- unsafe Neo4j DB name: skip
- non-empty dedicated Neo4j test DB after Wave 3 cleanup: hard failure
