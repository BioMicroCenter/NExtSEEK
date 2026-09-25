"""A model failure inside a granular op ends in the op's AGENT_FAILED envelope (F6, operator ruling 2026-09-25).

``LLMFatalError`` is a ``BaseException``, so ``_run_granular_op``'s ``except Exception`` never saw
it: a double 503 inside a CC op skipped the ``AGENT_FAILED`` envelope and the "granular op failed"
log, and the sidecar got whatever the server does with an uncaught ``BaseException``.

Pinned here:

* the op answers 502 with ``code: AGENT_FAILED``, the envelope every other failed op gets, and
  logs it the same way;
* when the models were unavailable, the reason ``model_unavailable`` leads the error's
  ``detail`` (the raw message follows it); any other fatal keeps its raw message as the detail;
* the reply needs no contract change: it is the envelope NExtSEEK's and the sidecar's
  ``OpErrorResponse`` already describe, the sidecar's HTTP client reads it as an agent failure
  that carries the reason, and the sidecar's answer to the plugin validates against both copies
  of the WS contract (``ns-sidecar/app/contract.py`` and the plugin's ``_ws_contract.py``).

The agents are patched: no model, no network.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from chat_nextseek.llm_clients import LLMFatalError
from NessieAI import paths

BASE = "/nextseek_api/assistant"
RAW = "All provider fallbacks exhausted: agent 'entity': 503 ServiceUnavailableException"
MOVE = [{"agent": "entity", "from": "gemini-3.5-flash", "to": "us.anthropic.claude-sonnet-4-6",
         "reason": "unavailable"}]


@contextmanager
def _sidecar_modules():
    """Import the sidecar package (``sidecar.app.*``) and the plugin's WS contract from the tree.

    Both are standalone in their images; here they are loaded from their files for the length
    of the block and removed afterwards.
    """
    with patch.dict(sys.modules):
        pkg = types.ModuleType("sidecar")
        pkg.__path__ = [str(paths.NS_SIDECAR_DIR)]
        sys.modules["sidecar"] = pkg
        spec = importlib.util.spec_from_file_location("_u7_plugin_ws_contract",
                                                      paths.CC_PLUGIN_BIN / "_ws_contract.py")
        plugin_contract = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = plugin_contract
        spec.loader.exec_module(plugin_contract)
        yield SimpleNamespace(
            server=importlib.import_module("sidecar.app.server"),
            ns_client=importlib.import_module("sidecar.app.ns_client"),
            exceptions=importlib.import_module("sidecar.app.exceptions"),
            granular_models=importlib.import_module("sidecar.app.granular_models"),
            contract=importlib.import_module("sidecar.app.contract"),
            plugin_contract=plugin_contract,
        )


class GranularOpFatalTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        for target, value in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", True),
            ("nextseek_api.services.assistant._granular_chat_config", SimpleNamespace()),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _entity_op_raising(self, fatal):
        with patch("chat_nextseek.portable.entity_agent", side_effect=fatal), \
             self.assertLogs("nextseek_api.services.assistant", level="ERROR") as logs:
            resp = self.client.post(f"{BASE}/entity/", {"query": "mouse"}, format="json")
        return resp, logs

    def test_an_unavailable_fatal_is_agent_failed_with_the_reason(self):
        resp, logs = self._entity_op_raising(
            LLMFatalError(RAW, agent="entity", unavailable=True, model_fallback=MOVE))
        self.assertEqual(resp.status_code, 502, resp.content)
        body = resp.json()
        self.assertEqual(body, {"code": "AGENT_FAILED",
                                "errors": [{"title": "AGENT_FAILED", "detail": f"model_unavailable: {RAW}"}]})
        self.assertTrue(any("granular op entity failed" in line for line in logs.output), logs.output)

    def test_any_other_fatal_is_agent_failed_with_its_raw_message(self):
        raw = "Unrecoverable LLM error: agent 'entity', model 'gemini-3.5-flash': 400 INVALID_ARGUMENT"
        resp, logs = self._entity_op_raising(LLMFatalError(raw, agent="entity"))
        self.assertEqual(resp.status_code, 502, resp.content)
        self.assertEqual(resp.json(), {"code": "AGENT_FAILED",
                                       "errors": [{"title": "AGENT_FAILED", "detail": raw}]})
        self.assertTrue(any("granular op entity failed" in line for line in logs.output), logs.output)

    def test_the_reply_fits_the_sidecar_and_plugin_contracts_unchanged(self):
        from nextseek_api.assistant.models_api import OpErrorResponse

        resp, _ = self._entity_op_raising(LLMFatalError(RAW, agent="entity", unavailable=True, model_fallback=MOVE))
        body = resp.json()
        OpErrorResponse.model_validate(body)

        with _sidecar_modules() as sc:
            sc.granular_models.OpErrorResponse.model_validate(body)

            # The sidecar's HTTP client reads it as an agent failure that carries the reason.
            http_reply = httpx.Response(resp.status_code, json=body,
                                        request=httpx.Request("POST", f"http://nextseek{BASE}/entity/"))
            with self.assertRaises(sc.exceptions.AgentFailedError) as caught:
                sc.ns_client._map_error(http_reply)
            self.assertIn("AGENT_FAILED", str(caught.exception))
            self.assertIn("model_unavailable", str(caught.exception))

            # The whole sidecar turn: the WS request in, the answer the plugin reads out.
            server = sc.server
            request_id = "6f1c1f6e-8a53-4c1b-9d8e-1a2b3c4d5e6f"
            raw_request = json.dumps({"op": "entity", "args": {"query": "mouse"},
                                      "ns_login": {"api_user": "u1", "api_pass": "p"},
                                      "request_id": request_id})
            with patch.object(server, "_build_user_config",
                              return_value=server.NsHttpConfig(base_url="http://nextseek", auth=("u1", "p"))), \
                 patch.object(server, "_build_write_gate", return_value=lambda *a, **k: None), \
                 patch.object(server, "_build_stage", return_value=lambda *a, **k: None), \
                 patch.object(server, "_build_stage_bytes", return_value=(lambda *a, **k: None, lambda *a, **k: None)), \
                 patch.object(sc.ns_client.httpx, "post", return_value=http_reply):
                answer = asyncio.run(server.handle_message(raw_request))

            for contract in (sc.contract, sc.plugin_contract):
                parsed = contract.SidecarResponse.model_validate_json(answer)
                self.assertEqual(parsed.status, "error")
                self.assertEqual(parsed.request_id, request_id)
                self.assertEqual(parsed.error.code, "AGENT_FAILED")
                self.assertEqual(contract.ERROR_EXIT[parsed.error.code], 4)
