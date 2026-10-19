"""run-harvest takes no --run-dir when the user names no folder ("my run is done").

The server then picks the caller's one waiting launched run, or returns the
candidates (NessieAI/tests/ns/reingest/test_run_harvest_pick.py). Every layer
between the agent and the server must therefore let the flag be absent: the op
registry, the sidecar's arg contract, its forwarded body, and both copies of
the request model.
"""
from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from NessieAI import paths

pytest.importorskip("httpx")
pytest.importorskip("pydantic")

_SIDECAR_DIR = paths.NESSIE_ROOT / "docker" / "ns-sidecar"


@pytest.fixture
def sidecar():
    package = types.ModuleType("sidecar")
    package.__path__ = [str(_SIDECAR_DIR)]
    saved = {name: mod for name, mod in sys.modules.items()
             if name == "sidecar" or name.startswith("sidecar.")}
    for name in saved:
        del sys.modules[name]
    sys.modules["sidecar"] = package
    try:
        yield SimpleNamespace(
            contract=importlib.import_module("sidecar.app.contract"),
            ops=importlib.import_module("sidecar.app.ops"),
            models=importlib.import_module("sidecar.app.granular_models"))
    finally:
        for name in [n for n in sys.modules if n == "sidecar" or n.startswith("sidecar.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_the_op_registry_marks_run_dir_optional():
    from NessieAI.cc.op_registry.ops import OPS

    spec = next(op for op in OPS if op.op_id == "run-harvest")
    run_dir = next(arg for arg in spec.argv if arg.flag == "--run-dir")
    assert run_dir.required is False


def test_the_sidecar_contract_accepts_no_run_dir(sidecar):
    args = sidecar.contract.validate_op_args("run-harvest", {})
    assert args.run_dir is None


def test_the_sidecar_forwards_no_run_dir_when_none_was_given(sidecar, monkeypatch):
    sent = {}
    monkeypatch.setattr(sidecar.ops.ns_client, "call_op",
                        lambda op, body, **kw: (sent.update(body), {"result": {}})[1])
    config = SimpleNamespace(base_url="http://x", auth=None)
    sidecar.ops._run_harvest({"run_dir": None}, config, None, None, None, None, None)
    assert "run_dir" not in sent
    sidecar.ops._run_harvest({"run_dir": "/net/x/runs/r"}, config, None, None, None, None, None)
    assert sent["run_dir"] == "/net/x/runs/r"


def test_both_request_models_accept_no_run_dir(sidecar):
    from nextseek_api.assistant.models_api import RunHarvestRequest

    assert RunHarvestRequest().run_dir is None
    assert sidecar.models.RunHarvestRequest().run_dir is None
