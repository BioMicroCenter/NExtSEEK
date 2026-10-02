"""An op's artifacts reach the chat under their real filename.

The download list names each file by its artifact key, which the download route
only accepts as word characters (``reingest_A.ALN.xlsx`` -> ``reingest_A_ALN``).
Staged under the key, a workbook lost its ``.xlsx`` and nothing opened it as
Excel. The server now sends each file's real name beside the key, and the
sidecar stages under that name, falling back to the key when none is sent.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from NessieAI import paths


@pytest.fixture
def sidecar(monkeypatch):
    """The sidecar's ``sidecar.app`` package, which exists only inside its image,
    aliased onto the source tree for the length of one test."""
    pkg = types.ModuleType("sidecar")
    pkg.__path__ = [str(paths.NS_SIDECAR_DIR)]
    monkeypatch.setitem(sys.modules, "sidecar", pkg)
    for name in [m for m in sys.modules if m.startswith("sidecar.")]:
        monkeypatch.delitem(sys.modules, name)
    staging = importlib.import_module("sidecar.app.staging")
    ops = importlib.import_module("sidecar.app.ops")
    contract = importlib.import_module("sidecar.app.contract")
    return types.SimpleNamespace(staging=staging, ops=ops, contract=contract)


def _stage_bytes(sidecar, tmp_path):
    cfg = types.SimpleNamespace(staging_dir=str(tmp_path))
    login = sidecar.contract.NsLogin(api_user="someone", api_pass="x")
    return sidecar.staging.make_stage_bytes(cfg, login, "req-1")


def test_an_artifact_is_staged_under_its_real_filename(sidecar, tmp_path):
    stage_bytes, _commit = _stage_bytes(sidecar, tmp_path)
    staged = stage_bytes("build-upload-xlsx", "reingest_A_ALN", b"PK", "reingest_A.ALN.xlsx")
    assert Path(staged).name == "reingest_A.ALN.xlsx"
    assert Path(staged).read_bytes() == b"PK"


def test_a_filename_never_leaves_the_request_dir(sidecar, tmp_path):
    stage_bytes, _commit = _stage_bytes(sidecar, tmp_path)
    staged = Path(stage_bytes("report", "k", b"x", "../../elsewhere/report.csv"))
    assert staged.name == "report.csv"
    assert staged.parent.name == "req-1"


def test_without_a_filename_the_key_still_names_the_file(sidecar, tmp_path):
    stage_bytes, _commit = _stage_bytes(sidecar, tmp_path)
    assert Path(stage_bytes("report", "merged_report", b"x")).name == "merged_report"


def test_build_upload_xlsx_stages_each_workbook_under_its_filename(sidecar, monkeypatch):
    envelope = {"result": {"saved_files": {}}, "download": {"artifacts": [
        {"key": "reingest_A_ALN", "url": "/a/", "filename": "reingest_A.ALN.xlsx"},
        {"key": "reingest_A_GEX", "url": "/b/", "filename": "reingest_A.GEX.xlsx"},
    ]}}
    monkeypatch.setattr(sidecar.ops.ns_client, "call_op", lambda *a, **k: envelope)
    monkeypatch.setattr(sidecar.ops.ns_client, "fetch_artifact", lambda *a, **k: b"PK")
    calls = []

    def stage_bytes(op, key, data, filename=None):
        calls.append((key, filename))
        return f"/staged/{filename or key}"

    config = types.SimpleNamespace(base_url="http://x", auth=None)
    result = sidecar.ops._build_upload_xlsx(
        {"manifest_id": "abc"}, config, None, None, None, stage_bytes, lambda: None)

    assert calls == [("reingest_A_ALN", "reingest_A.ALN.xlsx"),
                     ("reingest_A_GEX", "reingest_A.GEX.xlsx")]
    assert result["staged_files"]["reingest_A_ALN"] == "/staged/reingest_A.ALN.xlsx"


def test_the_download_list_carries_each_files_real_name():
    from nextseek_api.services.assistant import artifact_download_entries

    entries = artifact_download_entries(
        {"reingest_A_ALN": "/out/run/reingest_A.ALN.xlsx",
         "geo_seq_workbooks": ["/out/run/geo_1.xlsx", "/out/run/geo_2.xlsx"]},
        "/base")
    assert entries == [
        {"key": "reingest_A_ALN", "url": "/base/reingest_A_ALN/",
         "filename": "reingest_A.ALN.xlsx"},
        {"key": "geo_seq_workbooks", "url": "/base/geo_seq_workbooks/",
         "filename": "geo_1.xlsx"},
    ]
