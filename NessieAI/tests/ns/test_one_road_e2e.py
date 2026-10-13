"""Every Container-CC op on the direct road, end to end in one process (approach 1, piece 2).

The plugin's own client talks to Django's real op view through a transport that hands each request to Django's test
client in this thread: the test database is shared and nothing listens on a socket. The op handlers are stand-ins;
the pass, the allow table, the view, the error contract, the bundle and the artifact GET are real. The files the tool
downloads land in a turn's scratch and the turn's publish picks them up.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from NessieAI import paths
from NessieAI.ns import granular
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline

READ_RESULTS = {
    "entity": {"sampletypes": [], "assays": [], "keywords": [], "projects": []},
    "parse": {"mode": "graph_query"},
    "graph": {"plan": {"cypher": "MATCH (s) RETURN count(s) AS n"}, "result": {"ok": True, "data": [{"n": 3}]}},
    "graph-schema": {"source": "catalog", "schema": "", "vocabulary": ""},
    "aggregate": {"question": "q", "complete": True, "elapsed_s": 0.1, "deadline_s": 50.0, "parts": [], "notes": []},
    "api-read": {"endpoint": "/nextseek_api/projects/", "method": "GET", "api_plan": {}, "response": {}},
    "run-ls": {"run_dir": "/runs/r", "truncated": False, "tree": ""},
}
READ_BODIES = {
    "entity": {"query": "mice"}, "parse": {"query": "mice"}, "graph": {"query": "mice"},
    "graph-schema": {"types": "TIS"}, "aggregate": {"query": "how many"}, "api-read": {"parser_plan": "{}"},
    "run-ls": {"run_dir": "/runs/r"},
}


def _returns(result):
    def handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
        return dict(result)
    return handler


def _writes(name, content, extra=None):
    def handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
        path = Path(outputs_dir) / name
        path.write_bytes(content)
        return {**(extra or {}), "saved_files": {name.replace(".", "_"): str(path)}}
    return handler


HANDLERS = {
    **{op: _returns(result) for op, result in READ_RESULTS.items()},
    "report": _writes("summary.xlsx", b"report-bytes", {"summary": {}, "rows": {}}),
    "generate-submission": _writes("GEO.xlsx", b"geo-bytes",
                                   {"report_type": "GEO", "report": {"samples": [{"uid": "MUS-1", "title": "a"}]}}),
    "build-upload-xlsx": _writes("reingest_A.xlsx", b"sheet-bytes", {"qa": {}}),
}


class _DjangoTransport(httpx.BaseTransport):
    """Hands each request the plugin's client makes to Django's test client, in this thread."""

    def __init__(self):
        self.client = APIClient()
        self.seen: list[tuple[str, str, dict]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.seen.append((request.method, request.url.path, headers))
        extra = {"HTTP_AUTHORIZATION": headers["authorization"]} if "authorization" in headers else {}
        response = self.client.generic(request.method, request.url.raw_path.decode("ascii"), data=request.read(),
                                       content_type=headers.get("content-type", "application/octet-stream"),
                                       **extra)
        try:
            body = b"".join(response.streaming_content) if response.streaming else response.content
        finally:
            response.close()
        return httpx.Response(response.status_code, headers=list(response.items()), content=body, request=request)


@contextmanager
def _plugin():
    """The plugin's bin modules, imported from the tree for the block and removed afterwards."""
    with patch.dict(sys.modules):
        for name in ("_op_errors", "_turn_deadline", "_turn_pass", "_assistant_models", "_assistant_client",
                     "_ws_contract", "_sidecar_client", "_op_road"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(paths.CC_PLUGIN_BIN))
        try:
            yield SimpleNamespace(ac=importlib.import_module("_assistant_client"),
                                  tp=importlib.import_module("_turn_pass"),
                                  road=importlib.import_module("_op_road"))
        finally:
            sys.path.remove(str(paths.CC_PLUGIN_BIN))


class OneRoadEndToEnd(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.chat = ChatSession.objects.create(user=self.user)
        task = QueryTask.objects.create(session=self.chat, user=self.user, query="q", status="running")
        self.turn, self.raw = issue_pass(task=task, chat=self.chat, user=self.user, login=("u1", "p"))
        set_deadline(self.turn, time.time() + 180)
        self.outputs = Path(tempfile.mkdtemp())
        self.scratch = Path(tempfile.mkdtemp())
        self.output_mount = Path(tempfile.mkdtemp())
        for folder in (self.outputs, self.scratch, self.output_mount):
            self.addCleanup(shutil.rmtree, folder, True)
        env = {"NEXTSEEK_OUTPUTS_DIR": str(self.outputs), "NEXTSEEK_SCRATCH_DIR": str(self.scratch),
               "NEXTSEEK_CHAT_SESSION_ID": str(self.chat.session_id), "NEXTSEEK_TURN_PASS": self.raw,
               "NEXTSEEK_USERNAME": "u1"}
        patchers = [
            patch.dict(os.environ, env),
            patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True),
            patch("nextseek_api.services.assistant._granular_chat_config", return_value=SimpleNamespace()),
            patch("nextseek_api.services.assistant._granular_outputs_dir", side_effect=self._outputs_dir),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        for key in ("NEXTSEEK_CC_OPS_ROAD", "NEXTSEEK_CC_TURN_DEADLINE_EPOCH"):
            os.environ.pop(key, None)

    def _outputs_dir(self):
        folder = self.outputs / uuid.uuid4().hex
        folder.mkdir()
        return str(folder)

    def _call(self, plugin, op, body):
        bridge = _DjangoTransport()

        def factory():
            return plugin.ac.AssistantClient(base_url="http://testserver", assistant_prefix="nextseek_api/assistant",
                                             auth=plugin.tp.TurnPassAuth(self.raw), transport=bridge)

        return plugin.road.call(op, body, client_factory=factory), bridge

    def test_every_read_op_answers_on_the_direct_road_with_the_pass(self):
        with _plugin() as plugin, patch.dict(granular._HANDLERS, HANDLERS):
            for op, body in READ_BODIES.items():
                with self.subTest(op):
                    result, bridge = self._call(plugin, op, body)
                    self.assertEqual(result, READ_RESULTS[op])
                    [(method, path, headers)] = bridge.seen
                    self.assertEqual((method, path), ("POST", f"/nextseek_api/assistant/{op}/"))
                    self.assertEqual(headers["authorization"], f"NextseekTurn {self.raw}")

    def test_the_artifact_ops_land_in_the_asking_chat_and_in_scratch_and_the_turn_publishes_them(self):
        from NessieAI.cc import cc_engine

        before = cc_engine.snapshot_before(self.scratch, "u1")
        with _plugin() as plugin, patch.dict(granular._HANDLERS, HANDLERS):
            report, _ = self._call(plugin, "report", {"mode": "samples", "project": "p"})
            submission, _ = self._call(plugin, "generate-submission", {"type": "GEO", "uids": "MUS-1"})
            sheet, _ = self._call(plugin, "build-upload-xlsx", {"rows": "[]"})

        artifacts = self.scratch / "nextseek-artifacts"
        self.assertEqual(Path(report["saved_files"]["summary_xlsx"]), artifacts / "summary.xlsx")
        self.assertEqual((artifacts / "summary.xlsx").read_bytes(), b"report-bytes")
        self.assertEqual(set(submission["staged_files"]), {"GEO_xlsx", "all_tables"})
        self.assertEqual(Path(submission["staged_files"]["GEO_xlsx"]).read_bytes(), b"geo-bytes")
        self.assertEqual(Path(sheet["staged_files"]["reingest_A_xlsx"]).read_bytes(), b"sheet-bytes")

        self.chat.refresh_from_db()
        self.assertEqual([b["id"] for b in self.chat.results_history], [1, 2, 3])  # all in the asking chat
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

        published = cc_engine._publish_artifacts(self.scratch, self.output_mount, turn_id="t1",
                                                 output_logical_root=str(self.output_mount), before=before)
        self.assertTrue(published["artifacts"])
        landed = self.output_mount / "artifacts" / "t1" / "nextseek-artifacts"
        self.assertEqual((landed / "summary.xlsx").read_bytes(), b"report-bytes")

    def test_a_refused_argument_comes_back_as_its_field_and_exit_3(self):
        handlers = {op: handler for op, handler in HANDLERS.items() if op != "aggregate"}  # the real aggregate
        with _plugin() as plugin, patch.dict(granular._HANDLERS, handlers):
            with self.assertRaises(plugin.road.OpCallError) as caught:
                self._call(plugin, "aggregate", {"query": "how many", "parts": "not json SECRET-9"})
        error = caught.exception
        self.assertEqual((error.code, error.exit_code), ("VALIDATION", 3))
        self.assertEqual(error.errors, [{"field": "parts", "type": "invalid_json"}])
        self.assertNotIn("SECRET-9", error.message)

    def test_api_write_is_refused_for_a_turn_pass(self):
        with _plugin() as plugin, patch.dict(granular._HANDLERS, HANDLERS):
            with self.assertRaises(plugin.road.OpCallError) as caught:
                self._call(plugin, "api-write", {"parser_plan": "{}", "confirmed_write": True})
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("PASS_NOT_ALLOWED", 12))
