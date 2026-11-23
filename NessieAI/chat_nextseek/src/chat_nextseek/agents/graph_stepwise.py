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
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import cypher_text, graph_catalog, graph_context
from ..graph_contract import schema
from ..graph_retry import normalized_cypher, scope_repair_context, timed_out
from ..graph_review import VALUES_CAP
from ..helpers.tools.neo4j import is_scope_refusal, tool_neo4j_query
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
#: Distinct matching values find_word shows per type, most records first.
WORD_VALUES = 15
#: One find_word call's Neo4j time; types not reached in it are reported as not checked.
WORD_CALL_BUDGET_S = 15
#: Rows a look reads at most (a LIMIT is added to a look that has none); the tool then probes the true total.
LOOK_ROWS_READ = ROWS_SHOWN + 1
#: The loop's wall clock: past the soft mark the next model turn is told to finish, past the hard one the turn goes
#: back to the one-shot agent (whose own retries then start).
SOFT_BUDGET_S = 35
HARD_BUDGET_S = 75
#: Candidates find_project lists when no title is the name itself.
PROJECT_CANDIDATES = 8

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: The model catalog this package ships (``NessieAI/chat_nextseek/agent_model_catalog.json``), read when a box's own
#: catalog file has no tool-capable row for this agent.
PACKAGED_CATALOG = Path(__file__).resolve().parents[3] / "agent_model_catalog.json"


@lru_cache(maxsize=1)
def _packaged_rows() -> tuple[tuple[str, str], ...]:
    """``(provider, model)`` for this agent in the packaged catalog: its default profile's row, then its _fallback."""
    try:
        catalog = json.loads(PACKAGED_CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    rows = []
    for model, groups in ((catalog.get("default") or {}).get("models") or {}).items():
        for group in groups if isinstance(groups, list) else [groups]:
            if AGENT_KEY in (group.get("agents") or ()) and group.get("provider"):
                rows.append((group["provider"], model))
    fallback = (catalog.get("_fallback") or {}).get(AGENT_KEY) or {}
    if fallback.get("provider") and fallback.get("model"):
        rows.append((fallback["provider"], fallback["model"]))
    return tuple(rows)


def _model(config) -> tuple[Any, str, Any, str | None]:
    """``(client, model, thinking budget, note)`` for the loop. A box whose catalog file predates this agent has no
    row for it, and the config then lands it on the global model, which may have no tool surface (dev 11-23: a
    hand-made all-Sonnet catalog gave it the Gemini client). Then the packaged catalog's row is used, loudly, and
    ``note`` says so; with no tool-capable client at all the client is None."""
    client, model_name, thinking = config.get_agent_model(AGENT_KEY)
    if callable(getattr(client, "chat_with_tools", None)):
        return client, model_name, thinking, None
    for provider, name in _packaged_rows():
        alt = (getattr(config, "LLM_CLIENTS", None) or {}).get(provider)
        if callable(getattr(alt, "chat_with_tools", None)):
            note = (f"this box's model catalog has no tool-capable {AGENT_KEY} row ({model_name}); "
                    f"used the packaged row {name}")
            log.warning(note)
            return alt, name, None, note
    return None, model_name, thinking, None


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
    #: Loop state: statements Neo4j stopped for time (never sent again), and whether the one scope rewrite was used.
    timed_out: set = field(default_factory=set)
    scope_repaired: bool = False


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
                    f"each (at most {VALUES_CAP}; 'complete' says whether that is all of them). Use it before "
                    "filtering a field on a value you have not seen.",
     "input_schema": {"type": "object", "properties": {
         "sample_type": {"type": "string", "description": "The type code, e.g. MUS or D.SEQ."},
         "field": {"type": "string", "description": "The attribute name exactly as the type's section lists it."}},
         "required": ["sample_type", "field"]}},
    {"name": "find_word",
     "description": "For each named type: the stored values that contain a word or phrase, most records first, each "
                    "with how many records hold it and whether it holds the word as a whole word. Use it before "
                    "keeping or dropping a word no field is known to hold, and to tell the word itself from longer "
                    "words that contain its letters.",
     "input_schema": {"type": "object", "properties": {
         "word": {"type": "string"},
         "sample_types": {"type": "array", "items": {"type": "string"}, "maxItems": WORD_TYPES_MAX,
                          "description": "Type codes, e.g. TIS or D.SEQ."}},
         "required": ["word", "sample_types"]}},
    {"name": "find_project",
     "description": "The project, investigation or study a name is stored as in this graph. Same answer every time.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "finish",
     "description": "The final query, which returns the answer itself, and what it matches. Call it alone, after you "
                    "have read your looks. An empty cypher with the reason in explanation means the graph cannot "
                    "answer.",
     "input_schema": {"type": "object", "properties": {
         "cypher": {"type": "string"}, "explanation": {"type": "string"}, "parameters": {"type": "object"},
         "keyword_fields": {"type": "object"}},
         "required": ["cypher", "explanation"]}},
]


class _BadInput(ValueError):
    """A tool input the model can fix: sent back to it as the tool's error."""


def _mapping(value, name: str) -> dict:
    """A tool's object argument: a dict, a JSON object in a string, or nothing."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise _BadInput(f"{name} must be a JSON object") from None
    if not isinstance(value, dict):
        raise _BadInput(f"{name} must be a JSON object")
    return dict(value)


def _cut(value) -> Any:
    if isinstance(value, str) and len(value) > VALUE_CHARS:
        return value[:VALUE_CHARS] + "..."
    if isinstance(value, list):
        return [_cut(v) for v in value[:20]]
    if isinstance(value, dict):
        return {k: _cut(v) for k, v in value.items()}
    return value


def _total(result: dict):
    """The result's total when the tool knows it, else None (never the number of rows shown)."""
    rows = list(result.get("data") or [])
    if result.get("total") is not None:
        return result["total"]
    return None if result.get("truncated") or len(rows) >= LOOK_ROWS_READ else len(rows)


def _shown(result: dict) -> dict:
    """A tool_neo4j_query result as the model sees it: ok, the total (None when unknown), the first ``ROWS_SHOWN``
    rows, the error, a refusal's reasons."""
    if not result.get("ok"):
        scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
        return {"ok": False, "error": _cut(str(result.get("error") or "failed")),
                "timed_out": timed_out(result),
                "refused_because": list(scope.get("reasons") or ())[:5] if scope.get("decision") == "refused" else []}
    rows = list(result.get("data") or [])
    return {"ok": True, "total": _total(result), "rows": [_cut(r) for r in rows[:ROWS_SHOWN]]}


_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+(\d+|\$\w+)\s*;?\s*$", re.IGNORECASE)


def _capped(cypher: str) -> str:
    """A look reads at most ``LOOK_ROWS_READ`` rows: a statement with no trailing LIMIT gets one."""
    return cypher if _TRAILING_LIMIT.search(cypher) else f"{cypher.rstrip().rstrip(';')} LIMIT {LOOK_ROWS_READ}"


def _labels(ctx: _Ctx, codes) -> tuple[list[tuple[str, str]], list[str]]:
    """Each named type as ``(code, label)`` when the catalog has it; the codes it does not have. ``T_MUS`` and
    ``MUS`` both name MUS."""
    known = {getattr(row, "label", None): getattr(row, "title", None) for row in getattr(ctx.snapshot, "index", ())}
    found, unknown = [], []
    for raw in codes or ():
        code = str(raw or "").strip()
        title = code[2:] if code.startswith("T_") and code not in {t for t in known.values()} else code
        label = schema.type_label(title) if title else None
        if label in known:
            found.append((known[label] or title, label))
        elif code:
            unknown.append(code)
    return found, unknown


def _run(config, ctx: _Ctx, cypher: str, parameters: dict, **kw) -> dict:
    """One statement through the scope; a statement Neo4j stopped for time is never sent again."""
    key = normalized_cypher(cypher)
    if key in ctx.timed_out:
        return {"ok": False, "error": "This statement already ran out of time once; write a cheaper one.",
                "data": []}
    result = tool_neo4j_query(config, cypher, parameters, **kw)
    if timed_out(result):
        ctx.timed_out.add(key)
    return result


def _run_query(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    cypher, parameters = str(inp.get("cypher") or ""), _mapping(inp.get("parameters"), "parameters")
    cypher, problem = _guard(ctx, cypher, parameters)
    if problem:
        step.update(guard=True, ok=False)
        return json.dumps({"ok": False, "not_run": problem})
    cypher = _capped(cypher)
    t0 = time.perf_counter()
    result = _run(config, ctx, cypher, parameters, timeout_s=LOOK_TIMEOUT_S)
    shown = _shown(result)
    step.update(cypher=cypher, ms=int((time.perf_counter() - t0) * 1000), ok=bool(result.get("ok")),
                total=shown.get("total"))
    return json.dumps(shown, default=str)


def _field_values(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    found, unknown = _labels(ctx, [inp.get("sample_type")])
    attribute = str(inp.get("field") or "")
    if unknown or not found or not _NAME.match(attribute):
        step["ok"] = False
        return json.dumps({"ok": False, "error": "name one sample type code from the type index and one attribute "
                                                 "name from its section" + (f"; unknown type: {unknown}" if unknown else "")})
    from ..graph_review_counts import values_statement
    cypher, problem = _guard(ctx, values_statement(found[0][1], attribute), {})
    if problem:
        step.update(guard=True, ok=False)
        return json.dumps({"ok": False, "not_run": problem})
    t0 = time.perf_counter()
    result = _run(config, ctx, cypher, {}, timeout_s=LOOK_TIMEOUT_S)
    step.update(cypher=cypher, ms=int((time.perf_counter() - t0) * 1000), ok=bool(result.get("ok")))
    if not result.get("ok"):
        return json.dumps(_shown(result), default=str)
    values = [{"value": _cut(r.get("v")), "samples": r.get("n")} for r in result.get("data") or []]
    complete = len(values) < VALUES_CAP
    return json.dumps({"ok": True, "sample_type": found[0][0], "field": attribute, "values": values,
                       "complete": complete,
                       **({} if complete else {"note": f"Only the {VALUES_CAP} most frequent values are listed; a "
                                                       "value not listed may still be stored: check it with run_query."})},
                      default=str)


def word_statement(label: str) -> str:
    """The stored values of one type's records that contain ``$w`` (folded), each with how many records hold it, most
    first. One scan; the cap sits inside, so the tool runs no second scan for a total."""
    return (f"MATCH (s:{label}) WHERE toLower(s.search_text) CONTAINS $w "
            "UNWIND [v IN split(s.search_text, '\\n') WHERE toLower(v) CONTAINS $w] AS v "
            f"WITH v, count(DISTINCT s) AS n ORDER BY n DESC, v LIMIT {WORD_VALUES} RETURN v, n")


def _whole_word(value: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])", str(value or "").lower()) is not None


def _find_word(config, ctx: _Ctx, inp: dict, step: dict) -> str:
    word = str(inp.get("word") or "").strip()
    found, unknown = _labels(ctx, (inp.get("sample_types") or [])[:WORD_TYPES_MAX])
    if not word or not (found or unknown):
        step["ok"] = False
        return json.dumps({"ok": False, "error": "name a word and at least one sample type code"})
    out, t0, all_ok = [], time.perf_counter(), True
    for code, label in found:
        if time.perf_counter() - t0 > WORD_CALL_BUDGET_S:
            out.append({"sample_type": code, "not_checked": "time ran out in this look; ask again for this type"})
            all_ok = False
            continue
        result = _run(config, ctx, word_statement(label), {"w": word.lower()}, timeout_s=LOOK_TIMEOUT_S)
        if not result.get("ok"):
            out.append({"sample_type": code, **_shown(result)})
            all_ok = False
            continue
        values = [{"value": _cut(r.get("v")), "records": r.get("n"), "whole_word": _whole_word(r.get("v"), word)}
                  for r in result.get("data") or []]
        out.append({"sample_type": code, "values": values, "complete": len(values) < WORD_VALUES,
                    "records_with_the_whole_word": sum(v["records"] or 0 for v in values if v["whole_word"]),
                    "records_with_the_letters_only": sum(v["records"] or 0 for v in values if not v["whole_word"])})
    for code in unknown:
        out.append({"sample_type": code, "error": "not a type code in the type index"})
        all_ok = False
    step.update(ms=int((time.perf_counter() - t0) * 1000), ok=all_ok,
                statements=[word_statement(label) for _, label in found])
    return json.dumps({"ok": True, "word": word, "by_type": out,
                       "note": "Record counts are per value; a record holding two matching values counts in both."},
                      default=str)


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


class _HandBack(Exception):
    """The turn goes to the one-shot agent, for the reason given."""


def _finish(config, ctx: _Ctx, inp: dict, step: dict) -> tuple[StepwiseOutcome | None, str]:
    """``(outcome, "")`` when the final query is settled; ``(None, message)`` to send back to the model. Raises
    ``_HandBack`` for a finish with neither a query nor a reason."""
    cypher = str(inp.get("cypher") or "").strip()
    parameters = _mapping(inp.get("parameters"), "parameters")
    explanation = str(inp.get("explanation") or "")
    keyword_fields = _mapping(inp.get("keyword_fields"), "keyword_fields")
    base = dict(explanation=explanation, parameters=parameters, keyword_fields=keyword_fields,
                context_mode=one_shot.CONTEXT_CATALOG, project_titles=ctx.project_titles,
                fields_named=ctx.fields_named)
    if not cypher:
        if not explanation.strip():
            raise _HandBack("finish carried no query and no reason")
        step.update(ok=True, refusal=True)
        return StepwiseOutcome(plan=GraphAgentPlan(cypher="", **base)), ""
    cypher, problem = _guard(ctx, cypher, parameters)
    if problem:
        step.update(guard=True, ok=False)
        return None, json.dumps({"ok": False, "not_run": problem})
    t0 = time.perf_counter()
    result = _run(config, ctx, cypher, parameters)
    ms = int((time.perf_counter() - t0) * 1000)
    step.update(cypher=cypher, ms=ms, ok=bool(result.get("ok")))
    outcome = StepwiseOutcome(plan=GraphAgentPlan(cypher=cypher, **base), result=result, elapsed_ms=ms,
                              parameters=parameters)
    if is_scope_refusal(result):
        # One rewrite, told the check's own reasons, when the refusal is about how the statement is written; any
        # other refusal is final and goes to the graph turn's own refusal handling (member types, refused counts,
        # the graph_search fallback), as a one-shot refusal does.
        repair = None if ctx.scope_repaired else scope_repair_context(result)
        if repair is None:
            step["refused"] = True
            return outcome, ""
        ctx.scope_repaired = True
        return None, json.dumps({"ok": False, "refused": repair})
    if not result.get("ok"):
        return None, json.dumps({"ok": False, "final_query_failed": _shown(result)}, default=str)
    return outcome, ""


_LAST_STEP = ("This is your last step: call finish now, with your best query, or with an empty cypher and the reason "
              "only if the graph cannot answer.")


def stepwise_graph_agent(config, user_query: str, entity_result, parser_plan, refine_context: str | None = None
                         ) -> StepwiseOutcome:
    """Look, then finish, in at most ``MAX_STEPS`` model turns and ``HARD_BUDGET_S``. See the module docstring.
    Never raises: anything unexpected hands the turn back."""
    steps: list[dict] = []
    started = time.perf_counter()

    def back(reason: str) -> StepwiseOutcome:
        print(f"[GRAPH][STEPWISE] handing back to the one-shot agent: {reason}")
        return StepwiseOutcome(plan=None, steps=steps, fallback=reason)

    try:
        client, model_name, thinking, note = _model(config)
        if client is None:
            return back("no tool-capable model for the stepwise agent in this box's catalog or the packaged one")
        if note:
            steps.append({"tool": "_model", "note": note})
        entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else dict(entity_result or {})
        plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else dict(parser_plan or {})
        ctx = _context(config, user_query, entity_dict, plan_dict, refine_context)
        if ctx is None:
            return back("the live catalog is unavailable")
        return _loop(config, ctx, client, model_name, thinking, steps, started, back)
    except _HandBack as hb:
        return back(str(hb))
    except Exception as exc:  # noqa: BLE001 (a bug here must never cost the user the one-shot answer)
        return back(f"stepwise failed: {type(exc).__name__}: {str(exc)[:200]}")


def _loop(config, ctx: _Ctx, client, model_name, thinking, steps: list, started: float, back) -> StepwiseOutcome:
    messages: list[dict] = [{"role": "user", "content": ctx.opening}]
    for turn in range(MAX_STEPS):
        elapsed = time.perf_counter() - started
        if elapsed > HARD_BUDGET_S:
            return back(f"over the {HARD_BUDGET_S} s budget after {turn} steps")
        if turn == MAX_STEPS - 1 or elapsed > SOFT_BUDGET_S:
            note = {"type": "text", "text": _LAST_STEP}
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
                {"type": "text", "text": "Use a look tool, or call finish with the final query. Never write the "
                                         "query as text."}]})
            continue
        with_looks = any(b.get("name") != "finish" for b in uses)
        results = []
        for block in uses:
            name, inp = block.get("name"), block.get("input") or {}
            step: dict[str, Any] = {"tool": name, "input": _cut(inp)}
            steps.append(step)
            try:
                if not isinstance(inp, dict):
                    raise _BadInput("the tool input must be a JSON object")
                if name == "finish":
                    if with_looks:
                        step["ok"] = False
                        message = json.dumps({"ok": False, "error": "Read your looks first, then call finish alone."})
                    else:
                        outcome, message = _finish(config, ctx, inp, step)
                        if outcome is not None:
                            outcome.steps = steps
                            return outcome
                elif name in _LOOKS:
                    message = _LOOKS[name](config, ctx, inp, step)
                else:
                    message = json.dumps({"ok": False, "error": f"no tool named {name}"})
            except _HandBack:
                raise
            except Exception as exc:  # noqa: BLE001 (bad input or a broken look: the model's to work around)
                step["ok"] = False
                message = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
            results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": message})
        messages.append({"role": "user", "content": results})
    return back(f"no finish within {MAX_STEPS} steps")
