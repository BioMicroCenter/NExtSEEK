"""Plugin-mirror artifact-shape validation (drift regression).

The in-image plugin validates the server's ``query_complete`` event with
``_assistant_models.QueryCompleteEvent`` (``extra="forbid"`` + a discriminated
``Artifact`` union). The server (``nextseek_api/assistant/excel_export.py``)
emits three artifact shapes as raw dicts: ``table`` (optionally carrying
``truncated``/``total_rows``/``rows_returned``), ``file``, and — for GEO/report
workbooks — ``preview`` (with a ``sheets`` field). If the mirror's union omits a
shape, the whole turn fails with a pydantic ``ValidationError`` -> shim exit 4
(the 2026-07-05 GEO-submission failure; same drift class as A-4 / T13).

These tests pin every shape the server actually emits.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[3] / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin"


@pytest.fixture(scope="module")
def models():
    sys.path.insert(0, str(_BIN))
    try:
        mod = importlib.import_module("_assistant_models")
        importlib.reload(mod)
        yield mod
    finally:
        if str(_BIN) in sys.path:
            sys.path.remove(str(_BIN))


def _qce(models, artifacts):
    return models.QueryCompleteEvent(reply="ok", session_id="s1", artifacts=artifacts)


def test_plain_table_artifact_still_valid(models):
    ev = _qce(models, [{
        "artifact_type": "table", "key": "samples", "label": "Samples",
        "columns": ["uid"], "data": [{"uid": "MUS-1"}],
    }])
    assert ev.artifacts[0].artifact_type == "table"


def test_file_artifact_still_valid(models):
    ev = _qce(models, [{
        "artifact_type": "file", "key": "geo", "label": "GEO", "file_format": "xlsx",
    }])
    assert ev.artifacts[0].artifact_type == "file"


def test_truncated_table_artifact_valid(models):
    # excel_export appends truncated/total_rows when a table exceeds
    # MAX_INLINE_ROWS (200), and rows_returned from reporter_result.
    ev = _qce(models, [{
        "artifact_type": "table", "key": "big", "label": "Big",
        "columns": ["uid"], "data": [{"uid": "x"}],
        "truncated": True, "total_rows": 573, "rows_returned": 573,
    }])
    assert ev.artifacts[0].total_rows == 573


def test_geo_preview_artifact_valid(models):
    # excel_export.py: GEO reports emit artifact_type="preview" with sheets.
    ev = _qce(models, [{
        "artifact_type": "preview", "key": "geo_report_preview",
        "label": "GEO Report Preview",
        "sheets": [{"name": "Metadata", "columns": ["UID"], "data": [{"UID": "D.SEQ-1"}], "total_rows": 72}],
    }])
    assert ev.artifacts[0].artifact_type == "preview"
    assert ev.artifacts[0].sheets[0]["total_rows"] == 72
