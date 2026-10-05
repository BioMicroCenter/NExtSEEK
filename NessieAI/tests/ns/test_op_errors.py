"""The op error contract (approach 1, piece 2: ops answer with closed codes): a closed code, a closed reason, a fixed
message, and never the text of an exception or a value the caller sent."""
from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from pydantic import BaseModel, ValidationError
from rest_framework.test import APIClient

from chat_nextseek.failure_replies import MODEL_UNAVAILABLE_REASON
from chat_nextseek.llm_clients import LLMFatalError, LLMTimeoutError
from chat_nextseek.schemas.schema_helper import StructuredOutputError
from NessieAI.ns import aggregate, granular
from NessieAI.ns.write_gate import WriteBlockedError
from nextseek_api.assistant import op_errors
from nextseek_api.assistant.models_api import OpErrorResponse

BASE = "/nextseek_api/assistant"
SECRET = "Zq7-SECRET-VALUE"


class ContractTests(SimpleTestCase):
    def test_the_closed_lists(self):
        self.assertEqual(op_errors.CODES, ("VALIDATION", "WRITE_BLOCKED", "AUTH_FAILED", "PASS_NOT_ALLOWED", "BUSY",
                                           "TIME_UP", "AGENT_FAILED"))
        self.assertEqual(op_errors.REASONS, ("model_unavailable", "deadline", "bad_output", "internal"))
        self.assertEqual(op_errors.REASONS[0], MODEL_UNAVAILABLE_REASON)

    def test_the_statuses(self):
        self.assertEqual(op_errors.HTTP_STATUS, {"VALIDATION": 422, "WRITE_BLOCKED": 403, "AUTH_FAILED": 401,
                                                 "PASS_NOT_ALLOWED": 403, "BUSY": 429, "TIME_UP": 408,
                                                 "AGENT_FAILED": 502})

    def test_the_exit_numbers(self):
        self.assertEqual(op_errors.EXIT, {"VALIDATION": 3, "AGENT_FAILED": 4, "WRITE_BLOCKED": 5, "TRANSPORT_ERROR": 7,
                                          "AUTH_FAILED": 8, "STAGING_ERROR": 9, "BUSY": 10, "TIME_UP": 11,
                                          "PASS_NOT_ALLOWED": 12})

    def test_every_code_and_reason_has_its_fixed_message(self):
        for code in op_errors.CODES:
            if code == "AGENT_FAILED":
                continue
            response = op_errors.op_error(code, status=op_errors.HTTP_STATUS[code])
            self.assertEqual(response.status_code, op_errors.HTTP_STATUS[code])
            self.assertEqual(response.data["message"], op_errors.CODE_MESSAGES[code])
            self.assertIsNone(response.data["reason"])
            OpErrorResponse.model_validate(response.data)
        for reason in op_errors.REASONS:
            body = op_errors.op_error("AGENT_FAILED", reason=reason, status=502).data
            self.assertEqual((body["reason"], body["message"]), (reason, op_errors.REASON_MESSAGES[reason]))
            self.assertEqual(body["errors"], [{"title": "AGENT_FAILED", "detail": body["message"]}])
            OpErrorResponse.model_validate(body)

    def test_an_unknown_reason_is_internal(self):
        self.assertEqual(op_errors.op_error("AGENT_FAILED", reason="weird", status=502).data["reason"], "internal")

    def test_only_agent_failed_carries_a_reason(self):
        with self.assertRaises(ValueError):
            op_errors.op_error("BUSY", reason="deadline", status=429)

    def test_an_unknown_code_is_refused(self):
        with self.assertRaises(ValueError):
            op_errors.op_error("TEAPOT", status=418)

    def test_validation_lists_fields_and_types_only(self):
        class Model(BaseModel):
            mode: int

        try:
            Model.model_validate({"mode": SECRET})
        except ValidationError as exc:
            fields = op_errors.validation_fields(exc)
        self.assertEqual(fields, [{"field": "mode", "type": "int_parsing"}])
        body = op_errors.op_error("VALIDATION", fields=fields, status=422).data
        self.assertEqual(body["errors"], fields)
        self.assertNotIn(SECRET, json.dumps(body))

    def test_failure_reasons(self):
        cases = [
            (LLMFatalError("x", agent="entity", unavailable=True), "model_unavailable"),
            (LLMFatalError("x", agent="graph", reason="deadline"), "deadline"),
            (LLMFatalError("400 INVALID_ARGUMENT", agent="entity"), "internal"),
            (StructuredOutputError("bad json", raw_output="{", errors=[], model=BaseModel), "bad_output"),
            (LLMTimeoutError("timed out"), "model_unavailable"),
            (RuntimeError("boom"), "internal"),
        ]
        for exc, reason in cases:
            with self.subTest(type(exc).__name__):
                self.assertEqual(op_errors.failure_reason(exc), reason)

    def test_the_pass_refusals_are_the_same_bodies(self):
        """Plan 02's authentication refusals and the op view's are one envelope from one message set."""
        from nextseek_api.assistant.turn_pass_auth import PassNotAllowed, PassSessionNotAccepted, TurnPassAuthFailed

        session_field = [{"field": "session_id", "type": "not_accepted_with_turn_pass"}]
        self.assertEqual(TurnPassAuthFailed().detail, op_errors.op_error("AUTH_FAILED", status=401).data)
        self.assertEqual(PassNotAllowed().detail, op_errors.op_error("PASS_NOT_ALLOWED", status=403).data)
        self.assertEqual(PassSessionNotAccepted().detail,
                         op_errors.op_error("VALIDATION", fields=session_field, status=422).data)
        self.assertEqual(TurnPassAuthFailed().detail["errors"],
                         [{"title": "AUTH_FAILED", "detail": op_errors.CODE_MESSAGES["AUTH_FAILED"]}])

    def test_every_op_validation_error_names_its_field(self):
        """A new raise without field= would answer VALIDATION with no field named."""
        for module in (granular, aggregate):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "OpValidationError":
                    names = {kw.arg for kw in node.keywords}
                    self.assertTrue({"field", "error_type"} <= names,
                                    f"{module.__name__}:{node.lineno} raises OpValidationError without field=/error_type=")


class ViewErrorTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.outputs = tempfile.mkdtemp()
        for target, kwargs in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            ("nextseek_api.services.assistant._granular_chat_config", {"return_value": SimpleNamespace()}),
            ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": self.outputs}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _post(self, op, body, handler=None):
        handlers = {op: handler} if handler is not None else {}
        with patch.dict(granular._HANDLERS, handlers):
            return self.client.post(f"{BASE}/{op}/", body, format="json")

    def test_a_refused_request_field_is_named_and_its_value_is_not(self):
        resp = self._post("report", {"mode": SECRET, "project": "p"})
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], "VALIDATION")
        self.assertEqual(body["errors"], [{"field": "mode", "type": "value_error"}])
        self.assertNotIn(SECRET, json.dumps(body))

    def test_an_extra_field_is_named(self):
        resp = self._post("graph", {"query": "q", "cypher": SECRET})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"], [{"field": "cypher", "type": "extra_forbidden"}])
        self.assertNotIn(SECRET, resp.content.decode())

    def test_an_op_argument_the_op_refuses_is_named(self):
        resp = self._post("aggregate", {"query": "how many", "parts": f"not json {SECRET}"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"], [{"field": "parts", "type": "invalid_json"}])
        self.assertNotIn(SECRET, resp.content.decode())

    def test_a_blocked_write_says_so_without_the_endpoint(self):
        def handler(*args, **op_ctx):
            raise WriteBlockedError(f"POST /nextseek_api/samples/{SECRET}/ is not read-safe")

        resp = self._post("api-read", {"parser_plan": "{}"}, handler)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "WRITE_BLOCKED")
        self.assertNotIn(SECRET, resp.content.decode())

    def test_a_failed_op_is_agent_failed_without_its_text(self):
        def handler(*args, **op_ctx):
            raise RuntimeError(f"boom {SECRET}")

        with self.assertLogs("nextseek_api.services.assistant", level="ERROR"):
            resp = self._post("entity", {"query": "mice"}, handler)
        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual((body["code"], body["reason"]), ("AGENT_FAILED", "internal"))
        self.assertNotIn(SECRET, json.dumps(body))
        OpErrorResponse.model_validate(body)
