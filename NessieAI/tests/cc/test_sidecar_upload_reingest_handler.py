"""The ns-sidecar's upload-reingest handler refuses an unconfirmed call itself,
before anything is forwarded to NExtSEEK (defense in depth: NExtSEEK gates
again server-side).

The sidecar ships as the ``sidecar`` package (``COPY sidecar/app/``), so the
test maps that package name onto ``NessieAI/docker/ns-sidecar`` for the
duration of each test only.
"""
from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from NessieAI import paths

pytest.importorskip("httpx")
pytest.importorskip("pydantic")

_SIDECAR_DIR = paths.NESSIE_ROOT / "docker" / "ns-sidecar"


@pytest.fixture
def sidecar(monkeypatch):
    package = types.ModuleType("sidecar")
    package.__path__ = [str(_SIDECAR_DIR)]
    saved = {name: mod for name, mod in sys.modules.items()
             if name == "sidecar" or name.startswith("sidecar.")}
    for name in saved:
        del sys.modules[name]
    sys.modules["sidecar"] = package
    try:
        ops = importlib.import_module("sidecar.app.ops")
        write_gate = importlib.import_module("sidecar.app.write_gate")
        exceptions = importlib.import_module("sidecar.app.exceptions")
        yield SimpleNamespace(ops=ops, write_gate=write_gate, exceptions=exceptions)
    finally:
        for name in [n for n in sys.modules if n == "sidecar" or n.startswith("sidecar.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.mark.parametrize("confirmed", [None, False, "true", 1])
def test_an_unconfirmed_upload_is_blocked_and_never_forwarded(sidecar, confirmed):
    args = {"build_ids": "a" * 64}
    if confirmed is not None:
        args["confirmed_write"] = confirmed
    gate = sidecar.write_gate.build_gate()
    with patch.object(sidecar.ops.ns_client, "call_op") as call_op:
        with pytest.raises(sidecar.exceptions.WriteBlockedError):
            sidecar.ops._upload_reingest(args, SimpleNamespace(base_url="", auth=None),
                                         None, gate, None, None, None)
    call_op.assert_not_called()
