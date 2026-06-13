"""Validation tests for the granular-op request/response models (models_api.py).

These models are what dmac copies verbatim to build requests and parse responses,
and they back the schema-contract test. Free tests (SimpleTestCase).
"""
from django.test import SimpleTestCase
from pydantic import ValidationError

from nextseek_api.assistant.models_api import (
    ApiReadRequest,
    ApiWriteRequest,
    EntityOpRequest,
    EntityOpResponse,
    GraphOpResponse,
    ParseOpResponse,
    ReportOpRequest,
    ReportOpResponse,
    SubmissionRequest,
    ApiReadResponse,
    SubmissionResponse,
)


class RequestModelTests(SimpleTestCase):
    def test_entity_request_requires_query(self):
        with self.assertRaises(ValidationError):
            EntityOpRequest()
        self.assertEqual(EntityOpRequest(query="x").query, "x")

    def test_api_write_confirmed_defaults_false(self):
        req = ApiWriteRequest(parser_plan="{}")
        self.assertIs(req.confirmed_write, False)

    def test_api_write_accepts_bool_true(self):
        req = ApiWriteRequest(parser_plan="{}", confirmed_write=True)
        self.assertIs(req.confirmed_write, True)

    def test_api_write_rejects_string_true_strict(self):
        # The string "true" must NOT coerce to bool True (defense in depth with the gate).
        with self.assertRaises(ValidationError):
            ApiWriteRequest(parser_plan="{}", confirmed_write="true")

    def test_api_write_rejects_int_one_strict(self):
        with self.assertRaises(ValidationError):
            ApiWriteRequest(parser_plan="{}", confirmed_write=1)

    def test_api_read_requires_parser_plan(self):
        with self.assertRaises(ValidationError):
            ApiReadRequest()

    def test_report_request_rejects_bad_mode(self):
        with self.assertRaises(ValidationError):
            ReportOpRequest(mode="bogus", project="P")
        for m in ("samples", "protocols", "published", "rppr"):
            self.assertEqual(ReportOpRequest(mode=m, project="P").mode, m)

    def test_submission_request_rejects_bad_type(self):
        with self.assertRaises(ValidationError):
            SubmissionRequest(type="BOGUS", uids="X-1")
        for t in ("GEO", "SRA", "NFCORE_RNASEQ", "NFCORE_SCRNASEQ", "PRIDE"):
            self.assertEqual(SubmissionRequest(type=t, uids="X-1").type, t)

    def test_submission_request_requires_nonempty_uids(self):
        with self.assertRaises(ValidationError):
            SubmissionRequest(type="GEO", uids="  , ")


class ResponseModelTests(SimpleTestCase):
    def test_entity_response_validates_real_shape_with_extras(self):
        payload = {
            "op": "entity",
            "result": {
                "sampletypes": [{"code": "MUS", "name": "Mouse", "confidence": 0.9}],
                "assays": [],
                "keywords": ["NDMA"],
                "projects": [],
                "unexpected_extra": True,
            },
        }
        resp = EntityOpResponse.model_validate(payload)
        self.assertEqual(resp.result.sampletypes[0].code, "MUS")

    def test_parse_response_types_target_endpoint(self):
        payload = {
            "op": "parse",
            "result": {
                "mode": "new_search",
                "target_endpoint": "/nextseek_api/samples/advanced_search/",
                "intent_summary": "find mice",
                "filters": {"sampletype_code": "MUS"},
                "resolved": {"sampletypes": [{"code": "MUS"}]},
            },
        }
        resp = ParseOpResponse.model_validate(payload)
        self.assertEqual(resp.result.target_endpoint, "/nextseek_api/samples/advanced_search/")

    def test_graph_response_carries_plan_and_rows(self):
        payload = {
            "op": "graph",
            "result": {
                "plan": {"cypher": "MATCH (s:Sample) RETURN s", "explanation": "", "parameters": {}},
                "result": {"ok": True, "data": [{"uuid": "MUS-240101ABC-1"}]},
            },
        }
        resp = GraphOpResponse.model_validate(payload)
        self.assertEqual(resp.result.plan.cypher, "MATCH (s:Sample) RETURN s")
        self.assertEqual(resp.result.result["data"][0]["uuid"], "MUS-240101ABC-1")

    def test_api_read_response_shape(self):
        payload = {
            "op": "api-read",
            "result": {
                "endpoint": "/nextseek_api/samples/advanced_search/",
                "method": "POST",
                "api_plan": {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                             "requestBody": {}, "queryParameters": {}, "notes": ""},
                "response": {"ok": True, "data": {"rows": [{"uid": "MUS-240101ABC-1"}]}},
            },
        }
        resp = ApiReadResponse.model_validate(payload)
        self.assertEqual(resp.result.endpoint, "/nextseek_api/samples/advanced_search/")
        self.assertTrue(resp.result.response["ok"])

    def test_report_response_shape(self):
        payload = {
            "op": "report",
            "result": {
                "summary": {"summary_mode": "published"},
                "saved_files": {"published_report": "/tmp/x.json"},
                "rows": {"ok": True},
            },
        }
        resp = ReportOpResponse.model_validate(payload)
        self.assertEqual(resp.result.saved_files["published_report"], "/tmp/x.json")

    def test_submission_response_shape(self):
        payload = {
            "op": "generate-submission",
            "result": {
                "report_type": "GEO",
                "report": {"study": {"title": "S"}, "samples": [{"uid": "X-1"}]},
                "narrative": "n",
                "notes": "",
            },
        }
        resp = SubmissionResponse.model_validate(payload)
        self.assertEqual(resp.result.report_type, "GEO")
