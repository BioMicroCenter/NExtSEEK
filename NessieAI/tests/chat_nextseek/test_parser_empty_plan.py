"""An empty parser output is a failed parse, not a considered "unsupported".

CI at 33abf299 (2026-09-18, task ed4b2e3b, "What mice are treated with NDMA?"): the
forced tool call came back with stop_reason=tool_use on the first attempt and no
validation error, yet the plan was ``ParserPlan()`` to the byte: mode "unsupported",
empty intent_summary, empty notes, empty metadata. Every ParserPlan field has a
default, the default mode is "unsupported", extra keys are ignored, and the tool
schema published to Bedrock had no ``required`` list. So ``{}``, a plan wrapped under
an unknown key, a stringified plan, and a bare ``{"mode": "unsupported"}`` all
validate to that same object, skip the repair loop, skip the orchestrator's
``metadata.failure`` reply, and reach the researcher as "I can't turn that request
into a valid NExtSEEK operation yet. Reason from parser: No additional notes."

The prompt demands every key. An unsupported plan that states neither what the user
wanted nor why it cannot be done is not a decision, so it goes back through the
repair loop like any other output that does not carry the schema, and a parser that
never produces a plan is reported as a fault.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.llm_clients import LLMResponse, pydantic_to_tool_schema
from chat_nextseek.schemas import ParserPlan

NDMA = "What mice are treated with NDMA?"

GOOD_PLAN = json.dumps({
    "mode": "new_search",
    "target_endpoint": "/nextseek_api/samples/retrieve/",
    "intent_summary": "Find mouse samples treated with NDMA.",
    "filters": {"sampletype_code": "MUS", "assay_codes": [], "keywords": ["NDMA"], "uids": []},
    "notes": "",
})

# Every one of these validates, with no error, to exactly the plan the CI turn got.
PLANLESS_OUTPUTS = {
    "empty object": "{}",
    "plan under an unknown key": json.dumps({"parser_plan": json.loads(GOOD_PLAN)}),
    "plan as a string": json.dumps({"input": GOOD_PLAN}),
    "bare unsupported": json.dumps({"mode": "unsupported"}),
}


class _ScriptedBedrock:
    """Stands in for BedrockClient on the forced-tool path: replays one output per call."""

    provider = "bedrock"

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls: list[list[dict]] = []

    def chat_structured(self, *, messages, system, model, schema, schema_name, **kw):
        self.calls.append(messages)
        content = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        return LLMResponse(
            content=content, raw=None,
            usage={"prompt_tokens": 17942, "completion_tokens": 361},
            model=model, provider=self.provider,
            metadata={"stop_reason": "tool_use", "structured_via": "tool_use"},
        )

    def chat(self, **kw):  # the plain path must not be reached on a capable client
        raise AssertionError("the forced-tool path degraded to plain text")


class _Config:
    PARSER_SYSTEM_PROMPT = "You are the NExtSEEK Parser Agent."
    MIN_API_ENDPOINTS: list = []
    MIN_GRAPH_SCHEMA: dict = {}
    ENDPOINT_INDEX = None
    FORCE_PARSER_MODE = None
    LOG_DIR = "/tmp"
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP: dict = {}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client):
        self.LLM_CLIENT = client
        self.LLM_MODEL = "us.anthropic.claude-opus-4-7"
        self.LLM_CLIENTS = {"anth": client}

    def get_agent_model(self, agent):
        return self.LLM_CLIENT, self.LLM_MODEL, None


def _run_parser(client) -> ParserPlan:
    entity = {"sampletypes": [{"code": "MUS", "name": "Mouse"}],
              "assays": [{"code": "Mouse Challenge", "name": "Mouse Challenge"}],
              "keywords": ["NDMA"], "projects": [], "labs": [], "lab_codes": []}
    return parser_mod.parser_agent({"results_history": []}, _Config(client), NDMA, entity)


@pytest.mark.parametrize("output", list(PLANLESS_OUTPUTS.values()), ids=list(PLANLESS_OUTPUTS))
def test_a_planless_output_is_asked_again_instead_of_becoming_unsupported(output):
    client = _ScriptedBedrock([output, GOOD_PLAN])

    plan = _run_parser(client)

    assert plan.mode == "new_search"
    assert plan.filters.sampletype_code == "MUS"
    assert len(client.calls) == 2


def test_the_retry_tells_the_model_what_was_missing():
    client = _ScriptedBedrock(["{}", GOOD_PLAN])

    _run_parser(client)

    repair = client.calls[1][-1]
    assert repair["role"] == "user"
    assert "intent_summary" in repair["content"]


def test_a_parser_that_never_returns_a_plan_is_reported_as_a_fault():
    """The orchestrator answers a plan carrying metadata.failure with "something went
    wrong on our side", not with "your request is not supported"."""
    client = _ScriptedBedrock(["{}"])

    plan = _run_parser(client)

    assert plan.mode == "unsupported"
    assert plan.metadata.get("failure") == "parse_error"
    assert len(client.calls) == 3


def test_a_considered_unsupported_with_a_reason_is_kept():
    decided = json.dumps({
        "mode": "unsupported",
        "intent_summary": "",
        "notes": "Deleting samples is not something this assistant can do.",
    })
    client = _ScriptedBedrock([decided])

    plan = _run_parser(client)

    assert plan.mode == "unsupported"
    assert plan.notes.startswith("Deleting samples")
    assert "failure" not in plan.metadata
    assert len(client.calls) == 1


def test_an_unsupported_that_states_the_intent_is_kept():
    decided = json.dumps({"mode": "unsupported", "intent_summary": "Delete every sample."})
    client = _ScriptedBedrock([decided])

    plan = _run_parser(client)

    assert plan.mode == "unsupported"
    assert len(client.calls) == 1


# --------------------------------------------------------------------------
# call_llm_structured: the check is opt-in, so no other caller changes.
# --------------------------------------------------------------------------

def test_without_a_result_check_an_empty_object_is_still_accepted():
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _ScriptedBedrock(["{}"])
    plan = call_llm_structured(
        _Config(client), "q", ParserPlan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
    )
    assert plan == ParserPlan()
    assert len(client.calls) == 1


def test_a_result_check_that_keeps_failing_raises_structured_output_error():
    from chat_nextseek.schemas.schema_helper import StructuredOutputError, call_llm_structured

    client = _ScriptedBedrock(["{}"])
    with pytest.raises(StructuredOutputError):
        call_llm_structured(
            _Config(client), "q", ParserPlan,
            messages=[{"role": "user", "content": "q"}],
            client=client, model_name="m", agent_label="parser",
            result_check=lambda plan: "no plan",
        )
    assert len(client.calls) == 3


# --------------------------------------------------------------------------
# The schema the model is handed says the same thing the prompt does.
# --------------------------------------------------------------------------

def test_the_parser_tool_schema_requires_a_mode_and_an_intent_summary():
    """Without a required list, ``{}`` is a valid answer to the forced tool call."""
    schema = pydantic_to_tool_schema(ParserPlan)
    assert {"mode", "intent_summary"} <= set(schema.get("required", []))


def test_parser_plan_still_builds_from_its_defaults():
    """The timeout and parse-error fallbacks construct ParserPlan with no mode; the
    requirement lives in the published schema only, never in validation."""
    assert ParserPlan().mode == "unsupported"
    assert ParserPlan.model_validate({}).intent_summary == ""
    assert ParserPlan(notes="x", metadata={"failure": "parse_error"}).mode == "unsupported"
