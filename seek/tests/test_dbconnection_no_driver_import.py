"""dmac.dbconnection must import without the MySQLdb driver (its MySQL backend is gone)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dbconnection_imports_without_the_driver():
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="dmac.test_settings")
    code = "import sys; sys.modules['MySQLdb'] = None; import dmac.dbconnection"
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
