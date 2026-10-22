"""Product code must not import the test tree; the contract exempts only the nessie management command."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The nessie management command is the harness's own entry point (it drives
# runner, http_driver and bundle), so it is the one named exception.
ALLOWED = {"nextseek_api.management.commands.nessie -> NessieAI.tests.nessie_tests.*"}


def test_product_contract_ignores_no_harness_module_but_the_command():
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
    (c,) = [c for c in contracts if c["name"] == "Product code does not import the test tree"]
    left = {i for i in c["ignore_imports"] if "NessieAI.tests.nessie_tests" in i} - ALLOWED
    assert not left, left
