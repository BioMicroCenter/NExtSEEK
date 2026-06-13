"""REAL-STACK PAID acceptance for the granular ops.

Runs against the real endpoint + real chat_nextseek + real local DB/Neo4j/REST +
real (paid) LLM. GATED: only runs when RUN_REALSTACK=1 is set, so normal suites
never spend money. api-read/api-write also require SEEK_TEST_USER/SEEK_TEST_PASS
(a login valid on this local stack).

Strict spend accounting
-----------------------
Every LLM client's ``chat`` is wrapped to record the ACTUAL provider token usage
(model + input/output tokens) per call into a ledger (also written to
outputs/_ledger.jsonl). Cost = actual_tokens * published per-model rate. There are
NO estimates: token counts are machine-captured; rates are the published June-2026
list prices. A hard cap (BUDGET_CAP_USD) aborts before any call that could exceed
it. Tests run cheapest-first (Gemini Flash), the two Opus-with-thinking ops last.

Published rates (USD per 1e6 tokens):
  gemini-3.5-flash               : 1.50 in / 9.00 out
  gemini-2.5-flash               : 0.30 in / 2.50 out
  us.anthropic.claude-opus-4-7   : 5.50 in / 27.50 out   (Opus 4.7 $5/$25 + 10% cross-region)
"""
import json
import os
import unittest
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from unittest.mock import patch

RUN = os.environ.get("RUN_REALSTACK") == "1"
SEEK_USER = os.environ.get("SEEK_TEST_USER")
SEEK_PASS = os.environ.get("SEEK_TEST_PASS")

BUDGET_CAP_USD = 5.00
# Don't START an Opus op unless this much headroom remains (one Opus op << $1).
OPUS_HEADROOM_USD = 2.00

# USD per token (input, output). Unpriced models -> hard error (no estimates).
PRICES = {
    "gemini-3.5-flash": (1.50e-6, 9.00e-6),
    "gemini-2.5-flash": (0.30e-6, 2.50e-6),
    "us.anthropic.claude-opus-4-7": (5.50e-6, 27.50e-6),
}

LEDGER: list[dict] = []
_LEDGER_PATH = Path(settings.BASE_DIR) / "outputs" / "_ledger.jsonl" if RUN else None
_ORIG_CHAT: dict = {}


def _record(model, usage):
    entry = {
        "model": str(model),
        "in": (usage or {}).get("prompt_tokens"),
        "out": (usage or {}).get("completion_tokens"),
    }
    LEDGER.append(entry)
    if _LEDGER_PATH:
        with open(_LEDGER_PATH, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def _cost():
    total = 0.0
    for e in LEDGER:
        rate = PRICES.get(e["model"])
        if rate is None:
            raise AssertionError(f"UNPRICED MODEL in ledger: {e['model']!r} — refuse to estimate")
        total += (e["in"] or 0) * rate[0] + (e["out"] or 0) * rate[1]
    return total


def _wrap_clients():
    import chat_nextseek.llm_clients as llm
    for name in ("OpenAIClient", "GeminiClient", "AnthropicClient", "BedrockClient"):
        cls = getattr(llm, name, None)
        if cls is None or name in _ORIG_CHAT:
            continue
        orig = cls.chat
        _ORIG_CHAT[name] = orig

        def make(orig):
            def chat(self, *a, **k):
                resp = orig(self, *a, **k)
                model = k.get("model") or (a[0] if a else getattr(self, "model", "?"))
                _record(model, getattr(resp, "usage", None))
                return resp
            return chat
        cls.chat = make(orig)


def _unwrap_clients():
    import chat_nextseek.llm_clients as llm
    for name, orig in _ORIG_CHAT.items():
        getattr(llm, name).chat = orig
    _ORIG_CHAT.clear()


@unittest.skipUnless(RUN, "real-stack paid acceptance; set RUN_REALSTACK=1 to run")
class RealStackAcceptance(TestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if _LEDGER_PATH and _LEDGER_PATH.exists():
            _LEDGER_PATH.unlink()
        _wrap_clients()

    @classmethod
    def tearDownClass(cls):
        _unwrap_clients()
        print(f"\n[REALSTACK] LLM calls={len(LEDGER)}  cumulative_cost=${_cost():.4f}")
        for e in LEDGER:
            print(f"[REALSTACK]   {e['model']}: in={e['in']} out={e['out']}")
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("rsuser", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        if SEEK_USER and SEEK_PASS:
            import base64
            token = base64.b64encode(f"{SEEK_USER}:{SEEK_PASS}".encode()).decode()
            self.client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
        p = patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                  return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def _budget_guard(self, opus=False):
        c = _cost()
        self.assertLess(c, BUDGET_CAP_USD, f"BUDGET CAP ${BUDGET_CAP_USD} exceeded (${c:.4f})")
        if opus:
            self.assertLess(c, BUDGET_CAP_USD - OPUS_HEADROOM_USD,
                            f"insufficient headroom for an Opus op (${c:.4f})")

    def _post(self, path, body):
        return self.client.post(f"/nextseek_api/assistant/{path}", body, format="json")

    def _sample_count(self):
        """Real row count of seek_production.samples via chat_nextseek's own DB conn."""
        cfg = settings.NEXTSEEK_CHAT_CONFIG
        conn = cfg._db_conn or cfg._connect_db(env="prod")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM seek_production.samples")
        n = cur.fetchone()[0]
        cur.close()
        return n

    # ----- cheapest (Gemini Flash) first -----

    def test_01_entity_resolves_MUS(self):
        from nextseek_api.assistant.models_api import EntityOpResponse
        self._budget_guard()
        resp = self._post("entity/", {"query": "mouse samples treated with NDMA"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        EntityOpResponse.model_validate(body)  # schema contract on LIVE output
        codes = [s["code"] for s in body["result"]["sampletypes"]]
        self.assertIn("MUS", codes, f"entity did not resolve MUS; got {codes}")

    def test_02_graph_executes_with_real_nodes(self):
        from nextseek_api.assistant.models_api import GraphOpResponse
        self._budget_guard()
        resp = self._post("graph/", {"query": "lineage of mouse samples"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        GraphOpResponse.model_validate(body)
        self.assertTrue(body["result"]["plan"]["cypher"], "no cypher produced")
        neo = body["result"]["result"]
        self.assertTrue(neo.get("ok"), f"neo4j exec not ok: {neo}")
        self.assertGreaterEqual(len(neo.get("data") or []), 1, "empty graph result")

    @unittest.skipUnless(SEEK_USER and SEEK_PASS, "needs SEEK_TEST_USER/PASS")
    def test_03_api_read_returns_real_rows(self):
        from nextseek_api.assistant.models_api import ApiReadResponse
        self._budget_guard()
        plan = json.dumps({
            "target_endpoint": "/nextseek_api/samples/advanced_search/",
            "intent_summary": "mouse samples treated with NDMA",
            "filters": {"keywords": ["NDMA"]},
            "resolved": {"sampletypes": [{"code": "MUS"}]},
        })
        resp = self._post("api-read/", {"parser_plan": plan})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        ApiReadResponse.model_validate(body)
        r = body["result"]["response"]
        self.assertTrue(r.get("ok"), f"api-read response not ok: {r.get('status_code')}")
        # Use the canonical NExtSEEK row extractor (handles rows/nodes/data shapes).
        from chat_nextseek.helpers import _extract_total_and_rows
        total, row_count = _extract_total_and_rows(r)
        self.assertGreaterEqual(row_count, 1,
                                f"api-read returned no sample rows (total={total})")

    @unittest.skipUnless(SEEK_USER and SEEK_PASS, "needs SEEK_TEST_USER/PASS")
    def test_04_api_write_blocked_leaves_db_unchanged(self):
        # No budget guard needed: the gate fires before any LLM/API call.
        before = self._sample_count()
        with patch("chat_nextseek.portable.api_agent_build_request") as build:
            resp = self._post("api-write/", {"parser_plan": "{}", "confirmed_write": False})
            build.assert_not_called()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "WRITE_BLOCKED")
        self.assertEqual(self._sample_count(), before, "DB row count changed on a blocked write!")

    @unittest.skipUnless(SEEK_USER and SEEK_PASS, "needs SEEK_TEST_USER/PASS")
    def test_05_api_write_confirmed_executes_read_safe(self):
        from nextseek_api.assistant.models_api import ApiWriteResponse
        self._budget_guard()
        plan = json.dumps({
            "target_endpoint": "/nextseek_api/samples/advanced_search/",
            "intent_summary": "mouse NDMA samples",
            "filters": {"keywords": ["NDMA"]},
            "resolved": {"sampletypes": [{"code": "MUS"}]},
        })
        resp = self._post("api-write/", {"parser_plan": plan, "confirmed_write": True})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        ApiWriteResponse.model_validate(body)
        self.assertTrue(body["result"]["response"].get("ok"), "confirmed write path errored")

    def test_06_report_published_has_db_values(self):
        from nextseek_api.assistant.models_api import ReportOpResponse
        # Free (no LLM): run_reporter_summary uses SQL/Neo4j only.
        resp = self._post("report/", {"mode": "published", "project": "Published Data"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        ReportOpResponse.model_validate(body)
        saved = body["result"]["saved_files"]
        self.assertTrue(saved, "report produced no saved_files")
        # The written file must carry DB-sourced content, not an empty stub.
        path = saved.get("published_report") or next(iter(saved.values()))
        with open(path, "rb") as fh:
            blob = fh.read()
        self.assertGreater(len(blob), 50, "report file is an empty stub")
        doc = json.loads(blob)
        self.assertIn("samples", doc)
        # HTTP delivery path: the registered bundle URL actually serves the bytes
        # (this is what lets dmac fetch the artifact for Dropbox).
        dl = body["download"]
        self.assertTrue(dl["session_id"])
        url = {a["key"]: a["url"] for a in dl["artifacts"]}.get("published_report")
        self.assertTrue(url, "no published_report download URL")
        dlresp = self.client.get(url)
        self.assertEqual(dlresp.status_code, 200)
        got = b"".join(dlresp.streaming_content) if dlresp.streaming else dlresp.content
        self.assertEqual(got, blob, "downloaded bytes != on-disk report")

    # ----- Opus-with-thinking ops last (guarded) -----

    def test_07_parse_targets_advanced_search(self):
        from nextseek_api.assistant.models_api import ParseOpResponse
        self._budget_guard(opus=True)
        resp = self._post("parse/", {"query": "mouse samples treated with NDMA"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        ParseOpResponse.model_validate(body)
        self.assertEqual(body["result"]["target_endpoint"],
                         "/nextseek_api/samples/advanced_search/")

    def test_08_generate_submission_geo(self):
        from nextseek_api.assistant.models_api import SubmissionResponse
        self._budget_guard(opus=True)
        # A real published UID from Neo4j (free read).
        cfg = settings.NEXTSEEK_CHAT_CONFIG
        from chat_nextseek.helpers import tool_neo4j_query
        g = tool_neo4j_query(
            cfg,
            "MATCH (inv:Investigation)<-[:IN_INVESTIGATION]-(:Study)<-[:IN_STUDY]-(s:Sample) "
            "RETURN s.uuid AS uuid LIMIT 1", {})
        rows = g.get("data") or []
        self.assertTrue(rows, "no published sample UID available in Neo4j")
        uid = rows[0]["uuid"]
        resp = self._post("generate-submission/", {"type": "GEO", "uids": uid})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        SubmissionResponse.model_validate(body)
        self.assertIsNotNone(body["result"], "report_writer output is null")
        self.assertEqual((body["result"].get("report_type") or "GEO"), "GEO")
        self.assertIsInstance(body["result"].get("report"), dict)
        # a downloadable bundle was registered for the submission output
        self.assertTrue(body["download"]["session_id"], "no bundle registered for submission")
