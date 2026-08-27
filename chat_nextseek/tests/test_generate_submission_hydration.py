"""Hermetic regression test for the ``generate-submission`` op hydration bug.

The op lives in ``nextseek_api/assistant/granular.py`` (which normally requires
django/mysqlclient), but its chat_nextseek dependencies are imported lazily
inside the handler. We exploit that: we stub the ``nextseek_api.assistant``
package chain and the lazily-imported ``chat_nextseek.*`` modules, then import
``granular.py`` directly from its file path so this runs on the hermetic box.

Bug: ``_generate_submission`` built ``ReportWriterPlan(reporter_context={"uids":
uids})`` and handed it straight to ``report_writer_agent``. The report writer is
prompted with "metadata to use; do NOT fetch anything new", so with only bare
UID strings and no sample metadata it emits an all-null skeleton. The op must
first FETCH each UID's real metadata (json_metadata) — exactly what
``generate_report_outputs`` does on its combined path — and pass it under
``reporter_context["metadata"]``.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GRANULAR_PY = _REPO / "nextseek_api" / "assistant" / "granular.py"

# Real-ish sample metadata for the two UIDs from the bug report, shaped the way
# fetch_reporter_metadata (tool_nextseek_api_request) returns it.
_FETCHED_METADATA = {
    "ok": True,
    "status_code": 200,
    "data": {
        "total_samples": 2,
        "data": [
            {
                "sample_type": "SEQ",
                "samples": [
                    {
                        "uid": "D.SEQ-221031SHA-67-PUB",
                        "json_metadata": {
                            "Name": "SRR2225716x",
                            "File_PrimaryData": "SRR2225716x.fastq.gz",
                            "Scientist": "Sarah Nyquist",
                            "Protocol": "https://nextseek-dev.mit.edu/sops/76",
                            "Link_PrimaryData": "https://www.ncbi.nlm.nih.gov/sra/SRR2225716x",
                        },
                    },
                    {
                        "uid": "D.SEQ-221031SHA-65-PUB",
                        "json_metadata": {
                            "Name": "SRR2225714x",
                            "File_PrimaryData": "SRR2225714x.fastq.gz",
                        },
                    },
                ],
            }
        ],
    },
}


def _load_granular_with_stubs():
    """Import granular.py in isolation, stubbing its heavy dependencies.

    Returns (module, spies) where spies exposes the mocked
    fetch_reporter_metadata and report_writer_agent for assertions.
    """
    # 1) Stub the nextseek_api.assistant.write_gate import that granular does at
    #    module top level (avoids pulling django).
    saved = {k: sys.modules.get(k) for k in (
        "nextseek_api", "nextseek_api.assistant", "nextseek_api.assistant.write_gate",
        "chat_nextseek", "chat_nextseek.portable", "chat_nextseek.schemas",
        "chat_nextseek.schemas.chat", "chat_nextseek.helpers",
    )}

    pkg_ns = types.ModuleType("nextseek_api"); pkg_ns.__path__ = []
    pkg_assist = types.ModuleType("nextseek_api.assistant"); pkg_assist.__path__ = []
    mod_wg = types.ModuleType("nextseek_api.assistant.write_gate")

    class WriteBlockedError(Exception):
        pass

    mod_wg.WriteBlockedError = WriteBlockedError
    sys.modules["nextseek_api"] = pkg_ns
    sys.modules["nextseek_api.assistant"] = pkg_assist
    sys.modules["nextseek_api.assistant.write_gate"] = mod_wg

    # 2) Stub the chat_nextseek modules that _generate_submission lazily imports.
    fetch_spy = MagicMock(return_value=_FETCHED_METADATA)
    annotate_spy = MagicMock(side_effect=lambda config, md: md)  # passthrough

    class ReportWriterPlan:
        def __init__(self, report_type=None, reporter_context=None, notes=""):
            self.report_type = report_type
            self.reporter_context = reporter_context or {}
            self.notes = notes

    writer_return = MagicMock()
    writer_return.model_dump.return_value = {"report_type": "GEO", "report": {}, "narrative": ""}
    writer_spy = MagicMock(return_value=writer_return)

    pkg_cn = types.ModuleType("chat_nextseek"); pkg_cn.__path__ = []
    mod_portable = types.ModuleType("chat_nextseek.portable")
    mod_portable.report_writer_agent = writer_spy
    pkg_schemas = types.ModuleType("chat_nextseek.schemas"); pkg_schemas.__path__ = []
    mod_chat = types.ModuleType("chat_nextseek.schemas.chat")
    mod_chat.ReportWriterPlan = ReportWriterPlan
    mod_helpers = types.ModuleType("chat_nextseek.helpers")
    mod_helpers.fetch_reporter_metadata = fetch_spy
    mod_helpers.annotate_metadata_with_sampletypes = annotate_spy

    sys.modules["chat_nextseek"] = pkg_cn
    sys.modules["chat_nextseek.portable"] = mod_portable
    sys.modules["chat_nextseek.schemas"] = pkg_schemas
    sys.modules["chat_nextseek.schemas.chat"] = mod_chat
    sys.modules["chat_nextseek.helpers"] = mod_helpers

    spec = importlib.util.spec_from_file_location("granular_under_test", _GRANULAR_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    spies = types.SimpleNamespace(
        fetch=fetch_spy, annotate=annotate_spy, writer=writer_spy,
        writer_return=writer_return, ReportWriterPlan=ReportWriterPlan,
    )
    return module, spies, saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_generate_submission_hydrates_reporter_context_with_fetched_metadata():
    module, spies, saved = _load_granular_with_stubs()
    try:
        uids = "D.SEQ-221031SHA-67-PUB,D.SEQ-221031SHA-65-PUB"
        module._generate_submission(
            {"type": "GEO", "uids": uids, "query": ""},
            config=MagicMock(),
            session=MagicMock(),
            write_gate=MagicMock(),
            neo4j_exec=None,
            outputs_dir=None,
        )

        # The op MUST fetch the real sample metadata for the UIDs...
        assert spies.fetch.called, (
            "generate-submission never fetched sample metadata — the report "
            "writer will receive bare UIDs and emit an all-null skeleton"
        )
        fetched_uids = spies.fetch.call_args.args[1]
        assert fetched_uids == [
            "D.SEQ-221031SHA-67-PUB",
            "D.SEQ-221031SHA-65-PUB",
        ]

        # ...and hand it to the report writer under reporter_context["metadata"].
        assert spies.writer.called
        plan = spies.writer.call_args.args[2]
        ctx = plan.reporter_context
        assert "metadata" in ctx, (
            "reporter_context handed to report_writer_agent has no 'metadata' — "
            "GEO fields will come back null"
        )
        assert ctx["metadata"] == _FETCHED_METADATA
        # UIDs still present for traceability.
        assert ctx.get("uids") == [
            "D.SEQ-221031SHA-67-PUB",
            "D.SEQ-221031SHA-65-PUB",
        ]
    finally:
        _restore(saved)
