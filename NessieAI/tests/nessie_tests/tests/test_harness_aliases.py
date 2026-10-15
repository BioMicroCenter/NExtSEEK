"""The harness modules moved to NessieAI.nessie_harness; the old paths are the same module objects."""
import importlib
import subprocess
import sys

import pytest

NAMES = ["corpus", "export", "bayes_manifest", "collect", "manifest", "outage", "sources", "turn_cost"]


@pytest.mark.parametrize("name", NAMES)
def test_old_path_is_the_moved_module(name):
    old = importlib.import_module(f"NessieAI.tests.nessie_tests.{name}")
    new = importlib.import_module(f"NessieAI.nessie_harness.{name}")
    assert old is new


def test_python_dash_m_on_the_old_path_still_runs():
    r = subprocess.run([sys.executable, "-m", "NessieAI.tests.nessie_tests.export", "--help"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "usage" in r.stdout.lower(), r.stderr[-500:]
