"""run-harvest --bundle reaches the server through every layer between the agent and the op:
the op registry and its skill row, the shim and the runner, the sidecar's arg contract and its
forwarded body, both copies of the request model, and the NS turn's argument mapping."""
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
_BIN = paths.NESSIE_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin"


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


def _spec():
    from NessieAI.cc.op_registry.ops import OPS

    return next(op for op in OPS if op.op_id == "run-harvest")


def test_the_op_registry_takes_an_optional_bundle():
    bundle = next(arg for arg in _spec().argv if arg.flag == "--bundle")
    assert bundle.required is False


def test_the_skill_row_tells_the_agent_what_a_bundle_is():
    row = _spec().skill_row
    assert "--bundle <file name in /data/input/>" in row.input
    assert "a .tar.gz the user uploaded in this chat" in row.purpose
    assert "Admins only" in row.purpose


def test_the_shim_passes_bundle_to_the_runner():
    shim = (_BIN / "nextseek-run-harvest").read_text()
    assert "--bundle) BUNDLE=\"$2\"; shift 2 ;;" in shim
    assert 'set -- "$@" --bundle "$BUNDLE"' in shim


def test_the_runner_forwards_bundle(monkeypatch):
    sys.path.insert(0, str(_BIN))
    try:
        runner = importlib.import_module("_nextseek_runner")
    finally:
        sys.path.remove(str(_BIN))
    sent = {}
    monkeypatch.setattr(runner, "_call_op", lambda op, body: (sent.update(op=op, body=body), {})[1])
    runner._dispatch_run_harvest(SimpleNamespace(run_dir=None, bundle="sarek.tar.gz", allow_failed_run=False))
    assert sent == {"op": "run-harvest", "body": {"bundle": "sarek.tar.gz"}}


def test_the_sidecar_contract_accepts_bundle(sidecar):
    assert sidecar.contract.validate_op_args("run-harvest", {"bundle": "x.tar.gz"}).bundle == "x.tar.gz"


def test_the_sidecar_forwards_bundle_only_when_given(sidecar, monkeypatch):
    sent = []
    monkeypatch.setattr(sidecar.ops.ns_client, "call_op",
                        lambda op, body, **kw: (sent.append(dict(body)), {"result": {}})[1])
    config = SimpleNamespace(base_url="http://x", turn_pass="pass")
    sidecar.ops._run_harvest({"bundle": "x.tar.gz"}, config, None, None, None, None, None)
    sidecar.ops._run_harvest({"run_dir": "/net/x/runs/r"}, config, None, None, None, None, None)
    assert sent == [{"bundle": "x.tar.gz"}, {"run_dir": "/net/x/runs/r"}]


def test_both_request_models_take_bundle(sidecar):
    from nextseek_api.assistant.models_api import RunHarvestRequest

    assert RunHarvestRequest(bundle="x.tar.gz").bundle == "x.tar.gz"
    assert sidecar.models.RunHarvestRequest(bundle="x.tar.gz").bundle == "x.tar.gz"
    assert RunHarvestRequest().bundle is None


def test_the_ns_turn_maps_bundle_into_the_op_args():
    from NessieAI.ns.turn import _granular_args
    from nextseek_api.assistant.models_api import RunHarvestRequest

    assert _granular_args("run-harvest", RunHarvestRequest(bundle="x.tar.gz"))["bundle"] == "x.tar.gz"
