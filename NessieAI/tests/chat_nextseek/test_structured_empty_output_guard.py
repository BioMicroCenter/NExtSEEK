"""An output that names none of its schema's fields is a failed parse, for every agent.

Commit 33653e7e closed this for the single-path parser: every ParserPlan field has a
default and extra keys are ignored, so ``{}``, a plan nested under a key the schema
does not have, and a plan passed as a string under one all validate, skip the repair
loop, and reach the user as a considered answer. A forced tool call on Bedrock that
returns ``{}`` does exactly that.

Five more schemas validate from ``{}`` (EntityAgentOutput, MultiParserPlan,
APIRequestPlan, ReporterPlan, ReportWriterOutput), and each is the output of a
structured call. Each call now takes the same ``result_check``: an output that set
none of the schema's fields goes back through the repair turn, and when no attempt
carries one the agent's existing failure path runs. An output that names its fields,
even to say they are empty, is an answer and passes.

The API agent is the dangerous one: an empty plan was filled with the parser's
endpoint and an empty body and sent, which is the request its own parse-failure path
refuses for an endpoint that needs a body.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.agents.api import api_agent_build_request
from chat_nextseek.agents.entity import entity_agent
from chat_nextseek.agents.reporter import report_writer_agent, reporter_agent
from chat_nextseek.llm_clients import LLMResponse, pydantic_to_tool_schema
from chat_nextseek.schemas import (
    APIRequestPlan,
    EntityAgentOutput,
    MultiParserPlan,
    ReporterPlan,
    ReportWriterOutput,
)
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.chat import ReportWriterPlan

ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"


class _ScriptedBedrock:
    """Stands in for BedrockClient on the forced-tool path: replays one output per call."""

    provider = "bedrock"

    def __init__(self, outputs, plain="{}"):
        self._outputs = list(outputs)
        self._plain = plain
        self.calls: list[list[dict]] = []
        self.plain_calls = 0

    def chat_structured(self, *, messages, system, model, schema, schema_name, **kw):
        self.calls.append(messages)
        content = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        return LLMResponse(
            content=content, raw=None, usage=None, model=model, provider=self.provider,
            metadata={"stop_reason": "tool_use", "structured_via": "tool_use"},
        )

    def chat(self, **kw):
        # Only the entity agent's own raw fallback reaches this, after the structured
        # call has failed for good.
        self.plain_calls += 1
        return LLMResponse(content=self._plain, raw=None, usage=None,
                           model=kw.get("model"), provider=self.provider)


class _Config:
    ENTITY_SYSTEM_PROMPT = "You are the NExtSEEK Entity Agent."
    MULTI_PARSER_SYSTEM_PROMPT = "You are the NExtSEEK Multi Parser."
    API_AGENT_SYSTEM_PROMPT = "You are the NExtSEEK API Agent."
    REPORTER_SYSTEM_PROMPT = "You are the NExtSEEK Reporter."
    REPORT_WRITER_SYSTEM_PROMPT = "You are the NExtSEEK Report Writer."
    MIN_SAMPLETYPES: list = [{"SampleType": "MUS", "Name": "Mouse"}]
    MIN_ASSAYS: list = []
    MIN_PROJECTS: list = []
    LABS = None
    MIN_GRAPH_SCHEMA: dict = {}
    ENDPOINT_INDEX = None
    FORCE_PARSER_MODE = None
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP: dict = {}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client, log_dir):
        self.LLM_CLIENT = client
        self.LLM_MODEL = "us.anthropic.claude-opus-4-7"
        self.LLM_CLIENTS = {"anth": client}
        self.LOG_DIR = str(log_dir)
        # advanced_search takes a POST body: the endpoint an empty body must never reach.
        self.MIN_API_ENDPOINTS = [{
            "path": ADVANCED_SEARCH, "method": "POST",
            "request_body": {"filter_searchText": "", "filter_matchType": "PARTIAL"},
        }]

    def get_agent_model(self, agent):
        return self.LLM_CLIENT, self.LLM_MODEL, None

    def get_schema_for_endpoint(self, endpoint):
        return {"method": "POST", "methods": ["POST"]} if endpoint == ADVANCED_SEARCH else None


@pytest.fixture
def run(tmp_path):
    def _make(outputs, **kw):
        client = _ScriptedBedrock(outputs, **kw)
        return client, _Config(client, tmp_path)
    return _make


PLANLESS = {
    "empty object": "{}",
    "nested under an unknown key": json.dumps({"result": {"sampletypes": [{"code": "MUS"}]}}),
    "passed as a string": json.dumps({"input": '{"sampletypes": [{"code": "MUS"}]}'}),
}


# --------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------

@pytest.mark.parametrize("output", list(PLANLESS.values()), ids=list(PLANLESS))
def test_an_output_that_names_no_field_is_rejected(output):
    value = EntityAgentOutput.model_validate(json.loads(output))
    assert value == EntityAgentOutput()
    assert schema_helper.empty_output_problem(value)


def test_an_output_that_names_its_fields_empty_is_an_answer():
    value = EntityAgentOutput.model_validate(
        {"sampletypes": [], "assays": [], "keywords": [], "projects": [], "labs": []}
    )
    assert schema_helper.empty_output_problem(value) is None


def test_the_reason_names_the_schema():
    assert "APIRequestPlan" in schema_helper.empty_output_problem(APIRequestPlan.model_validate({}))


# --------------------------------------------------------------------------
# Entity agent (both structured calls)
# --------------------------------------------------------------------------

GOOD_ENTITY = json.dumps({"sampletypes": [{"code": "MUS", "name": "Mouse"}], "assays": [],
                          "keywords": ["NDMA"], "projects": [], "labs": []})


@pytest.mark.parametrize("output", list(PLANLESS.values()), ids=list(PLANLESS))
def test_entity_agent_asks_again_after_a_planless_output(run, output):
    client, config = run([output, GOOD_ENTITY])

    result = entity_agent(config, "What mice are treated with NDMA?", [], [], [])

    assert [s.code for s in result.sampletypes] == ["MUS"]
    assert len(client.calls) == 2
    assert "EntityAgentOutput" in client.calls[1][-1]["content"]


def test_entity_agent_keeps_an_explicitly_empty_extraction(run):
    empty = json.dumps({"sampletypes": [], "assays": [], "keywords": [], "projects": [], "labs": []})
    client, config = run([empty])

    result = entity_agent(config, "How many samples are there?", [], [], [])

    assert result.sampletypes == []
    assert len(client.calls) == 1


def test_entity_retry_after_a_timeout_is_guarded_too(run, monkeypatch):
    from chat_nextseek.agents import entity as entity_mod
    from chat_nextseek.llm_clients import LLMTimeoutError

    real = entity_mod.call_llm_structured
    seen: list[dict] = []

    def _first_times_out(*args, **kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise LLMTimeoutError("slow")
        return real(*args, **kwargs)

    monkeypatch.setattr(entity_mod, "call_llm_structured", _first_times_out)
    client, config = run(["{}"])

    result = entity_agent(config, "mice", [], [], [])

    assert result.sampletypes == []
    assert seen[0].get("result_check") is schema_helper.empty_output_problem
    assert seen[1].get("result_check") is schema_helper.empty_output_problem


# --------------------------------------------------------------------------
# Multi-parser (_canonical_multi_parse)
# --------------------------------------------------------------------------

GOOD_MULTI = json.dumps({
    "intent_summary": "Find mouse samples treated with NDMA.",
    "resolved": {"sampletypes": [{"code": "MUS", "name": "Mouse"}], "assays": [],
                 "keywords": ["NDMA"], "projects": []},
    "candidates": [{"mode": "new_search", "target_endpoint": ADVANCED_SEARCH,
                    "rationale": "attribute search", "confidence": 0.9}],
    "notes": "",
})

MULTI_PLANLESS = {
    **PLANLESS,
    "every field named and empty": json.dumps(
        {"intent_summary": "", "resolved": {}, "candidates": [], "notes": ""}
    ),
}


def _multi(config):
    entity = {"sampletypes": [{"code": "MUS", "name": "Mouse"}], "assays": [],
              "keywords": ["NDMA"], "projects": [], "labs": [], "lab_codes": []}
    return parser_mod._canonical_multi_parse(
        {"results_history": []}, config, "What mice are treated with NDMA?", entity
    )


@pytest.mark.parametrize("output", list(MULTI_PLANLESS.values()), ids=list(MULTI_PLANLESS))
def test_multi_parser_asks_again_after_a_planless_output(run, output):
    client, config = run([output, GOOD_MULTI])

    plan = _multi(config)

    assert [c.mode for c in plan.candidates] == ["new_search"]
    assert plan.intent_summary.startswith("Find mouse")
    assert len(client.calls) == 2


def test_a_multi_parser_that_never_plans_takes_its_failure_path(run):
    client, config = run(["{}"])

    plan = _multi(config)

    assert "multi_parser_agent failed" in plan.notes
    assert len(client.calls) == 3


def test_a_considered_multi_plan_is_kept_first_time(run):
    client, config = run([GOOD_MULTI])

    _multi(config)

    assert len(client.calls) == 1


def test_the_multi_parser_tool_schema_requires_an_intent_and_candidates():
    schema = pydantic_to_tool_schema(MultiParserPlan)
    assert {"intent_summary", "candidates"} <= set(schema.get("required", []))


def test_multi_parser_plan_still_builds_from_its_defaults():
    """The fallback builds MultiParserPlan from parts; the requirement lives in the
    published schema only."""
    assert MultiParserPlan().candidates == []
    assert MultiParserPlan.model_validate({}).intent_summary == ""


# --------------------------------------------------------------------------
# API agent: an empty plan must not go out as an empty body
# --------------------------------------------------------------------------

PARSER_PLAN = {
    "mode": "new_search", "target_endpoint": ADVANCED_SEARCH,
    "intent_summary": "Find mouse samples treated with NDMA.",
    "filters": {"sampletype_code": "MUS", "keywords": ["NDMA"]},
}
GOOD_API = json.dumps({"endpoint": ADVANCED_SEARCH, "method": "POST",
                       "requestBody": {"filter_searchText": "NDMA", "sampletype": "MUS"},
                       "queryParameters": {}, "notes": ""})


@pytest.mark.parametrize("output", list(PLANLESS.values()), ids=list(PLANLESS))
def test_an_empty_api_plan_is_refused_not_sent_with_an_empty_body(run, output):
    client, config = run([output])

    plan = api_agent_build_request(config, PARSER_PLAN)

    assert plan.endpoint is None
    assert "no request was attempted" in plan.notes
    assert len(client.calls) == 3


def test_api_agent_asks_again_after_an_empty_plan(run):
    client, config = run(["{}", GOOD_API])

    plan = api_agent_build_request(config, PARSER_PLAN)

    assert plan.endpoint == ADVANCED_SEARCH
    assert plan.requestBody["filter_searchText"] == "NDMA"
    assert len(client.calls) == 2


# --------------------------------------------------------------------------
# Reporter and report writer
# --------------------------------------------------------------------------

GOOD_REPORTER = json.dumps({"project": "IMPACT", "years": [2024], "month_range": None,
                            "day_range": None, "notes": "IMPACT for 2024"})


def test_reporter_asks_again_after_an_empty_plan(run):
    client, config = run(["{}", GOOD_REPORTER])

    plan = reporter_agent(config, "Run a report for IMPACT in 2024")

    assert plan.project == "IMPACT"
    assert len(client.calls) == 2


def test_a_reporter_that_never_plans_takes_its_failure_path(run):
    client, config = run(["{}"])

    plan = reporter_agent(config, "Run a report for IMPACT in 2024")

    assert plan.notes.startswith("Reporter could not produce a structured plan.")
    assert len(client.calls) == 3


def test_a_reporter_plan_that_names_its_fields_is_kept(run):
    """"Find all samples uploaded in 2025" is a real plan with no project."""
    decided = json.dumps({"project": None, "years": [2025], "month_range": None,
                          "day_range": None, "notes": "all projects"})
    client, config = run([decided])

    reporter_agent(config, "Find all samples uploaded in 2025")

    assert len(client.calls) == 1


GOOD_WRITER = json.dumps({"report_type": "PRIDE", "report": {"project": {"title": "x"}},
                          "narrative": None, "notes": ""})


def test_report_writer_asks_again_after_an_empty_report(run):
    client, config = run(["{}", GOOD_WRITER])

    out = report_writer_agent(config, "Export for PRIDE", ReportWriterPlan(report_type=None))

    assert out.report == {"project": {"title": "x"}}
    assert len(client.calls) == 2


def test_a_report_writer_that_never_writes_takes_its_failure_path(run):
    client, config = run(["{}"])

    out = report_writer_agent(config, "Export for PRIDE", ReportWriterPlan(report_type=None))

    assert out.notes == "Report writer could not produce structured output."
    assert len(client.calls) == 3


def test_the_guarded_schemas_are_exactly_the_ones_that_validate_from_nothing():
    """If a schema here stops validating `{}`, its guard is redundant; if another
    structured schema starts to, it needs one."""
    for model in (EntityAgentOutput, MultiParserPlan, APIRequestPlan, ReporterPlan,
                  ReportWriterOutput):
        assert model.model_validate({}).model_fields_set == set(), model.__name__
