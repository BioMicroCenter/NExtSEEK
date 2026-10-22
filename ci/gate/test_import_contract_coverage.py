"""The engine/Django boundary is a lint-imports contract, not only a unit test."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = {"NessieAI.ns", "NessieAI.router", "NessieAI.cc", "NessieAI.schema_rag", "NessieAI.hibayes"}


def _contracts():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]


def test_a_forbidden_contract_keeps_nextseek_api_out_of_the_engine():
    hits = [
        c for c in _contracts()
        if c["type"] == "forbidden"
        and ENGINE <= set(c["source_modules"])
        and "nextseek_api" in c["forbidden_modules"]
    ]
    assert hits, "no forbidden contract from the five engine packages to nextseek_api"
