"""R4: the full migration chain must apply on a non-MySQL (sqlite) backend."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_full_migration_chain_applies_and_builds_schema_on_sqlite():
    script = (
        "import os, django;"
        "os.environ['DJANGO_SETTINGS_MODULE']='dmac.test_settings';"
        "django.setup();"
        "from django.core.management import call_command;"
        "call_command('migrate','--noinput','--verbosity','0');"
        "from django.apps import apps;"
        "from django.db import connection;"
        "cs=apps.get_model('nextseek_api','ChatSession');"
        "cols={c.name for c in connection.introspection.get_table_description(connection.cursor(), cs._meta.db_table)};"
        "assert 'extra_state' in cols, sorted(cols);"
        "print('SCHEMA_OK', cs._meta.db_table)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"migrate/introspect failed on sqlite:\n{combined}"
    assert "SCHEMA_OK" in proc.stdout
    assert "information_schema" not in combined
    assert 'near "CHARACTER"' not in combined
