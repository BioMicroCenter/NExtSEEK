"""Import contracts (pyproject.toml [tool.importlinter]) hold: no new leaks."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold():
    r = subprocess.run(["lint-imports"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
