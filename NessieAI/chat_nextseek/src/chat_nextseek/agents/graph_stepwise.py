"""The stepwise graph agent: it looks at the data, then writes the final query.

The one-shot graph agent (``agents/graph.py``) writes one statement from the schema and guesses what it cannot see: how
a status is spelled, whether a word is in the records, which title a project is stored under. Code checks the result
afterwards and retries once on a zero. This agent is given look tools and a finish tool in one tool-calling
conversation (the pipeline agent's machinery, ``tool_loop.call_tools``), so it can check a guess before the answer is
final, for any field and any word, not only the guesses a rule was written for.

It runs only when the switch is on (``GRAPH_AGENT_MODE`` = ``stepwise``, env ``NESSIE_GRAPH_AGENT_MODE``); the
one-shot agent is unchanged and still answers every other turn. What it shares with the one-shot agent:

* the prompt (``graph_agent.txt``) and the live catalog context, plus ``graph_stepwise_agent.txt`` on how to look;
* the guards: every statement the model writes passes the same property, whole-node, procedure, shape, UID and
  OPTIONAL MATCH checks before it may run, and a problem goes back to the model instead of running;
* the scope: every statement, the look tools' own included, runs through ``tool_neo4j_query``.

It hands the turn back to the one-shot agent (``StepwiseOutcome.plan`` None, ``fallback`` saying why) when the model
has no tool surface, the live catalog is unavailable, the model fails, or no ``finish`` comes within ``MAX_STEPS``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .. import cypher_text, graph_catalog, graph_context
from ..graph_contract import schema
from ..helpers.tools.neo4j import tool_neo4j_query
from ..schemas.graph import GraphAgentPlan
from ..tool_loop import call_tools
from . import graph as one_shot

log = logging.getLogger(__name__)

AGENT_KEY = "graph_stepwise"
MODE_STEPWISE = "stepwise"
#: Model turns in one graph step, the finish included. The last one is told to finish.
MAX_STEPS = 6
#: Rows of a look shown to the model; the total is always said.
ROWS_SHOWN = 20
#: A value shown to the model is cut to this many characters.
VALUE_CHARS = 160
#: Each look statement's Neo4j time limit (the final query keeps the tool's own).
LOOK_TIMEOUT_S = 10
#: Types one find_word may name.
WORD_TYPES_MAX = 6
#: Records whose matching values find_word shows, per type.
WORD_EXAMPLES = 3
#: Candidates find_project lists when no title is the name itself.
PROJECT_CANDIDATES = 8

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def enabled(config) -> bool:
    """The switch: on only when the config says exactly ``stepwise`` (a MagicMock config is off)."""
    return getattr(config, "GRAPH_AGENT_MODE", None) == MODE_STEPWISE


@dataclass
class StepwiseOutcome:
    """What the stepwise agent settled on. ``plan`` None means hand the turn to the one-shot agent."""

    plan: GraphAgentPlan | None
    result: dict | None = None      # the final statement's tool_neo4j_query result; None for a refusal
    elapsed_ms: int = 0             # the final statement's Neo4j time
    parameters: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)   # every tool call: tool, input, what came back, ms
    fallback: str | None = None


@dataclass
class _Ctx:
    """The conversation's fixed parts and what the guards need."""

    system: str
    opening: str
    snapshot: Any
    scoped: bool
    procedures: frozenset
    project_titles: dict
    fields_named: dict


# --- context and guards ------------------------------------------------------------------------------------------


def _context(config, user_query: str, entity_dict: dict, plan_dict: dict, refine: str | None = None) -> _Ctx | None:
    """The system text (the graph prompt, this mode's addendum, the schema head) and the opening message (the
    resolved types, the parser plan, the vocabulary, the turn's notes, the question). None when the live catalog is
    unavailable: the one-shot agent's committed-schema path answers then."""
    ctx = one_shot.resolve_catalog_context(config, user_query, entity_dict, plan_dict, reader="stepwise graph agent")
    if not isinstance(ctx, one_shot.CatalogContext):
        return None
    scoped = not graph_catalog.shows_committed_vocabulary(config)
    schema_text = one_shot.schema_heading(getattr(ctx.snapshot, "schema_version", None)) + ctx.schema
    head, found, sections = schema_text.partition("\n\n" + graph_context.RESOLVED_TYPES_HEADING)
    addendum = (config._load_prompt("graph_stepwise_agent.txt")
                .replace("{max_steps}", str(MAX_STEPS)).replace("{word_types}", str(WORD_TYPES_MAX)))
    system = "\n\n".join([one_shot.render_hidden_properties(config.GRAPH_AGENT_SYSTEM_PROMPT, scoped), addendum, head])
    if plan_dict and one_shot._projects_parser_plan(config):
        upstream = one_shot.PROJECTED_PLAN_HEADING + "\n" + json.dumps(one_shot.project_parser_plan(plan_dict), indent=2)
    elif plan_dict:
        upstream = "PARSER PLAN (from Parser Agent: routing intent + resolved entities + filters):\n" + json.dumps(
            plan_dict, indent=2)
    else:
        upstream = "RESOLVED ENTITIES (from Entity Agent, no parser plan available):\n" + json.dumps(entity_dict, indent=2)
    parts = [graph_context.RESOLVED_TYPES_HEADING + sections if found else "", upstream,
             one_shot.VOCABULARY_HEADING + ctx.vocabulary if ctx.vocabulary else "", refine or "",
             "QUESTION:\n" + user_query]
    return _Ctx(system=system, opening="\n\n".join(p for p in parts if p), snapshot=ctx.snapshot, scoped=scoped,
                procedures=cypher_text.variant_procedures(config), project_titles=dict(ctx.project_titles),
                fields_named=dict(ctx.fields_named))


def _guard(ctx: _Ctx, cypher: str, parameters: dict) -> tuple[str, str | None]:
    """The one-shot agent's guards on a statement the model wrote: ``(statement to run, None)``, or the statement and
    the repair message naming every problem (nothing runs then)."""
    cypher, _ = one_shot.canonicalize_sample_uid_property(cypher)
    problems = one_shot._property_problems(cypher, ctx.snapshot)
    whole = one_shot.whole_node_returns(cypher, ctx.procedures)
    calls = one_shot._procedure_problems(cypher, ctx.procedures)
    shapes = one_shot.query_shape_problems(cypher, parameters, ctx.scoped)
    if problems or whole or calls or shapes:
        return cypher, one_shot._catalog_repair_message(problems, whole, ctx.snapshot, calls, shapes, cypher)
    guarded, _ = one_shot.repair_optional_match_filters(cypher)
    return guarded or cypher, None


# --- tools -------------------------------------------------------------------------------------------------------

TOOLS = [
    {"name": "run_query",
     "description": "Run one read-only Cypher statement and see the result: the total and the first rows. Use it to "
                    "count, compare or try a filter before the final query.",
     "input_schema": {"type": "object", "properties": {
         "cypher": {"type": "string"}, "parameters": {"type": "object"},
         "purpose": {"type": "string", "description": "What this look checks, in a few words."}},
         "required": ["cypher"]}},
    {"name": "field_values",
     "description": "The values one field stores on one sample type, most frequent first, with how many samples hold "
                    "each. Use it before filtering a field on a value you have not seen.",
     "input_schema": {"type": "object", "properties": {
         "sample_type": {"type": "string", "description": "The type code, e.g. MUS or D.SEQ."},
         "field": {"type": "string", "description": "The attribute name exactly as the type's section lists it."}},
         "required": ["sample_type", "field"]}},
    {"name": "find_word",
     "description": "How many records of each named type hold a word or phrase anywhere in their values, and the "
                    "stored values that hold it on a few of them. Use it before keeping or dropping a word no field "
                    "is known to hold, and to see whether a match is the word itself or a longer one.",
     "input_schema": {"type": "object", "properties": {
         "word": {"type": "string"},
         "sample_types": {"type": "array", "items": {"type": "string"}, "maxItems": WORD_TYPES_MAX}},
         "required": ["word", "sample_types"]}},
    {"name": "find_project",
     "description": "The project, investigation or study a name is stored as in this graph. Same answer every time.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "finish",
     "description": "The final query, which returns the answer itself, and what it matches. An empty cypher with the "
                    "reason in explanation means the graph cannot answer.",
     "input_schema": {"type": "object", "properties": {
         "cypher": {"type": "string"}, "explanation": {"type": "string"}, "parameters": {"type": "object"},
         "keyword_fields": {"type": "object"}},
         "required": ["cypher", "explanation"]}},
]


def _cut(value) -> Any:
    if isinstance(value, str) and len(value) > VALUE_CHARS:
        return value[:VALUE_CHARS] + "..."
    if isinstance(value, list):
        return [_cut(v) for v in value[:20]]
    if isinstance(value, dict):
        return {k: _cut(v) for k, v in value.items()}
    return value


def _shown(result: dict) -> dict:
    """A tool_neo4j_query result as the model sees it: ok, the total, the first ``ROWS_SHOWN`` rows, the error and a
    refusal's reasons."""
    if not result.get("ok"):
        scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
        return {"ok": False, "error": _cut(str(result.get("error") or "failed")),
                "refused_because": list(scope.get("reasons") or ())[:5] if scope.get("decision") == "refused" else []}
    rows = list(result.get("data") or [])
    total = result.get("total") if result.get("total") is not None else result.get("count", len(rows))
    return {"ok": True, "total": total, "truncated": bool(result.get("truncated")),
            "rows": [_cut(r) for r in rows[:ROWS_SHOWN]]}


def _label(code: str) -> str | None:
    code = str(code or "").strip()
    return schema.type_label(code) if code else None


def _run_query(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    cypher, parameters = str(inp.get("cypher") or ""), dict(inp.get("parameters") or {})
    cypher, problem = _guard(ctx, cypher, parameters)
    if problem:
        step["guard"] = True
        return json.dumps({"ok": False, "not_run": problem})
    t0 = time.perf_counter()
    result = tool_neo4j_query(config, cypher, parameters, timeout_s=LOOK_TIMEOUT_S)
    step.update(cypher=cypher, ms=int((time.perf_counter() - t0) * 1000), ok=bool(result.get("ok")),
                total=_shown(result).get("total"))
    return json.dumps(_shown(result), default=str)


def _field_values(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    label, attribute = _label(inp.get("sample_type")), str(inp.get("field") or "")
    if not label or not _NAME.match(attribute):
        return json.dumps({"ok": False, "error": "name one sample type code and one attribute name"})
    from ..graph_review_counts import values_statement
    cypher = values_statement(label, attribute)
    cypher, problem = _guard(ctx, cypher, {})
    if problem:
        return json.dumps({"ok": False, "not_run": problem})
    t0 = time.perf_counter()
    result = tool_neo4j_query(config, cypher, {}, timeout_s=LOOK_TIMEOUT_S)
    step.update(cypher=cypher, ms=int((time.perf_counter() - t0) * 1000), ok=bool(result.get("ok")))
    if not result.get("ok"):
        return json.dumps(_shown(result), default=str)
    values = [{"value": _cut(r.get("v")), "samples": r.get("n")} for r in result.get("data") or []]
    return json.dumps({"ok": True, "sample_type": inp.get("sample_type"), "field": attribute, "values": values},
                      default=str)


def _matching_values(text: Any, word: str) -> list[str]:
    """The stored values (``search_text`` holds one per line) that contain ``word``, case folded."""
    w = word.lower()
    return [_cut(line) for line in str(text or "").splitlines() if w in line.lower()][:5]


def _find_word(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    word = str(inp.get("word") or "").strip()
    codes = [str(c) for c in (inp.get("sample_types") or []) if str(c).strip()][:WORD_TYPES_MAX]
    if not word or not codes:
        return json.dumps({"ok": False, "error": "name a word and at least one sample type"})
    out, t0 = [], time.perf_counter()
    for code in codes:
        label = _label(code)
        count = tool_neo4j_query(config, f"MATCH (s:{label}) WHERE toLower(s.search_text) CONTAINS $w "
                                         "RETURN count(s) AS n", {"w": word.lower()}, timeout_s=LOOK_TIMEOUT_S)
        if not count.get("ok"):
            out.append({"sample_type": code, **_shown(count)})
            continue
        n = ((count.get("data") or [{}])[0] or {}).get("n", 0)
        entry: dict[str, Any] = {"sample_type": code, "records": n}
        if n:
            rows = tool_neo4j_query(config, f"MATCH (s:{label}) WHERE toLower(s.search_text) CONTAINS $w "
                                            f"RETURN s.uuid AS uuid, s.search_text AS text ORDER BY s.id "
                                            f"LIMIT {WORD_EXAMPLES}", {"w": word.lower()},
                                    timeout_s=LOOK_TIMEOUT_S)
            entry["examples"] = [{"uuid": r.get("uuid"), "values_holding_it": _matching_values(r.get("text"), word)}
                                 for r in rows.get("data") or []] if rows.get("ok") else []
        out.append(entry)
    step.update(ms=int((time.perf_counter() - t0) * 1000), ok=True)
    return json.dumps({"ok": True, "word": word, "by_type": out}, default=str)


def _containers(config) -> tuple[dict, list]:
    """The titles the caller may see, by level, and the projects catalog rows."""
    vocab = graph_catalog.get_vocabulary(config)
    levels = {"study": tuple(vocab.study_titles), "investigation": tuple(vocab.investigation_titles),
              "project": tuple(vocab.project_titles)}
    rows = []
    for name in ("FULL_PROJECTS_MAP", "FULL_INVESTIGATIONS_MAP"):
        mapping = getattr(config, name, None)
        if isinstance(mapping, dict):
            rows += list(mapping.values())
    return levels, rows


def _find_project(config, inp: dict, step: dict | None = None) -> str:
    """The container a name is stored as, by the projects catalog's rule (``graph_context.container_titles_for``),
    and when that names none, the titles whose folded form holds the name or is held by it. Deterministic."""
    name = str(inp.get("name") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "name a project, investigation or study"})
    levels, rows = _containers(config)
    hit = graph_context.container_titles_for([name], rows, levels).get(name)
    resolved = {"level": hit[0], "title": hit[1]} if hit else None
    key = graph_context._fold_title(name)
    candidates = []
    for level in graph_context.CONTAINER_LEVELS:
        for title in sorted(levels.get(level) or ()):
            folded = graph_context._fold_title(title)
            if key and folded and (key in folded or folded in key):
                candidates.append({"level": level, "title": title})
    if step is not None:
        step.update(ok=True, resolved=resolved)
    return json.dumps({"ok": True, "name": name, "resolved": resolved,
                       "candidates": candidates[:PROJECT_CANDIDATES]})


_LOOKS = {"run_query": _run_query, "field_values": _field_values, "find_word": _find_word,
          "find_project": lambda config, ctx, inp, step: _find_project(config, inp, step)}


# --- the loop ----------------------------------------------------------------------------------------------------


def _finish(config, ctx: _Ctx, inp: dict, step: dict) -> tuple[StepwiseOutcome | None, str]:
    """``(outcome, "")`` when the final query is settled; ``(None, message)`` to send back to the model."""
    cypher = str(inp.get("cypher") or "").strip()
    parameters = dict(inp.get("parameters") or {})
    explanation = str(inp.get("explanation") or "")
    keyword_fields = inp.get("keyword_fields") if isinstance(inp.get("keyword_fields"), dict) else {}
    base = dict(explanation=explanation, parameters=parameters, keyword_fields=keyword_fields,
                context_mode=one_shot.CONTEXT_CATALOG, project_titles=ctx.project_titles,
                fields_named=ctx.fields_named)
    if not cypher:
        step.update(ok=True, refusal=True)
        return StepwiseOutcome(plan=GraphAgentPlan(cypher="", **base)), ""
    cypher, problem = _guard(ctx, cypher, parameters)
    if problem:
        step["guard"] = True
        return None, json.dumps({"ok": False, "not_run": problem})
    t0 = time.perf_counter()
    result = tool_neo4j_query(config, cypher, parameters)
    ms = int((time.perf_counter() - t0) * 1000)
    step.update(cypher=cypher, ms=ms, ok=bool(result.get("ok")))
    if not result.get("ok"):
        return None, json.dumps({"ok": False, "final_query_failed": _shown(result)}, default=str)
    return StepwiseOutcome(plan=GraphAgentPlan(cypher=cypher, **base), result=result, elapsed_ms=ms,
                           parameters=parameters), ""


def stepwise_graph_agent(config, user_query: str, entity_result, parser_plan, refine_context: str | None = None
                         ) -> StepwiseOutcome:
    """Look, then finish, in at most ``MAX_STEPS`` model turns. See the module docstring."""
    steps: list[dict] = []

    def back(reason: str) -> StepwiseOutcome:
        print(f"[GRAPH][STEPWISE] handing back to the one-shot agent: {reason}")
        return StepwiseOutcome(plan=None, steps=steps, fallback=reason)

    client, model_name, thinking = config.get_agent_model(AGENT_KEY)
    if not callable(getattr(client, "chat_with_tools", None)):
        return back("the stepwise model has no tool surface in this profile")
    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else dict(entity_result or {})
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else dict(parser_plan or {})
    try:
        ctx = _context(config, user_query, entity_dict, plan_dict, refine_context)
    except Exception as exc:  # noqa: BLE001 (the one-shot agent has its own fallback path)
        return back(f"context failed: {type(exc).__name__}: {exc}")
    if ctx is None:
        return back("the live catalog is unavailable")

    messages: list[dict] = [{"role": "user", "content": ctx.opening}]
    for turn in range(MAX_STEPS):
        if turn == MAX_STEPS - 1:
            note = {"type": "text", "text": "This is your last step: call finish now."}
            last = messages[-1]
            if isinstance(last.get("content"), list):
                last["content"].append(note)
            else:
                messages.append({"role": "assistant", "content": [{"type": "text", "text": "(looking)"}]})
                messages.append({"role": "user", "content": [note]})
        try:
            resp = call_tools(config, messages=messages, tools=TOOLS, system=ctx.system, model_name=model_name,
                              client=client, agent_label=AGENT_KEY, thinking_budget=thinking)
        except Exception as exc:  # noqa: BLE001 (LLMFatalError and anything else: the one-shot agent answers)
            return back(f"model call failed: {type(exc).__name__}: {exc}")
        content = resp.get("content") or []
        uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        messages.append({"role": "assistant", "content": content})
        if not uses:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Use a look tool, or call finish with the final query."}]})
            continue
        results = []
        for block in uses:
            name, inp = block.get("name"), block.get("input") or {}
            step: dict[str, Any] = {"tool": name, "input": _cut(inp)}
            steps.append(step)
            if name == "finish":
                outcome, message = _finish(config, ctx, inp, step)
                if outcome is not None:
                    outcome.steps = steps
                    return outcome
            elif name in _LOOKS:
                try:
                    message = _LOOKS[name](config, ctx, inp, step)
                except Exception as exc:  # noqa: BLE001 (a look that breaks is the model's to work around)
                    message = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
            else:
                message = json.dumps({"ok": False, "error": f"no tool named {name}"})
            results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": message})
        messages.append({"role": "user", "content": results})
    return back(f"no finish within {MAX_STEPS} steps")
