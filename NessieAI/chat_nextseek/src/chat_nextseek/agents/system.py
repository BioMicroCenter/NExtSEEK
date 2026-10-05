from __future__ import annotations

import json
from typing import Any

from .. import graph_catalog, system_tools
from ..chat_memory import history_block, recent_turns
from ..config import ChatConfig
from ..context_rows import is_investigation_row, is_project_row
from ..llm_clients import without_reasoning_blocks
from ..tool_loop import call_tools
from ..schemas import (
    EntityAgentOutput,
    ParserPlan,
    SystemAgentOutput,
)
from .graph import live_catalog_context


def _as_map(value) -> dict:
    """`value` when it is a dict, else an empty one."""
    return value if isinstance(value, dict) else {}



def _lab_details(config, entity_dict: dict, projects_map: dict) -> dict:
    """ENTITY_DETAILS entries for the SEEK labs the question names, from ``config.LABS``.

    "Who is Engelward?" was answered from the model's general knowledge (a professorship, a
    directorship) and never from the lab record NExtSEEK holds (local run 2026-09-22,
    bucket5.who_is_a_person): the labs never reached this agent. A lab is matched by a code the
    entity step resolved (``lab_codes``, ``lab_matches``) or by a name it read as a scientist,
    lab or keyword that equals a lab's name. Nothing when ``LABS`` is not a list.
    """
    labs = getattr(config, "LABS", None)
    if not isinstance(labs, list):
        return {}
    codes = {c.upper() for c in entity_dict.get("lab_codes") or [] if isinstance(c, str)}
    for match in entity_dict.get("lab_matches") or []:
        code = match.get("code") if isinstance(match, dict) else getattr(match, "code", None)
        if isinstance(code, str):
            codes.add(code.upper())
    names = {
        n.strip().lower()
        for key in ("scientists", "labs", "keywords")
        for n in entity_dict.get(key) or []
        if isinstance(n, str) and n.strip()
    }
    project_names = {
        row.get("project_id"): name for name, row in projects_map.items() if isinstance(row, dict)
    }
    details: dict = {}
    for record in labs:
        if not isinstance(record, dict):
            continue
        code, name = record.get("code"), record.get("name")
        if not (isinstance(code, str) and isinstance(name, str)):
            continue
        if code.upper() not in codes and name.strip().lower() not in names:
            continue
        project_ids = list(record.get("project_ids") or [])
        details[f"{name} lab ({code})"] = {
            "entity_type": "lab",
            "code": code,
            "name": name,
            "title": record.get("title"),
            "affiliation": record.get("affiliation"),
            "project_ids": project_ids,
            "projects": [project_names[pid] for pid in project_ids if pid in project_names],
        }
    return details


#: Lookups before the answer. A system question needs a docs page or a catalog row or two; one that cannot
#: answer in four calls has misread the question rather than run short.
MAX_ITER = 4

SYSTEM_AGENT_KEY = "system"


def _caller_text(config) -> str:
    """The CALLER block: the signed-in user's own session, set on the per-request config by the server
    (``nextseek_api.graph_search.scope.caller_block``) and read from nothing else. Not a dict, or empty: absent."""
    caller = getattr(config, "CALLER", None)
    if not isinstance(caller, dict) or not caller:
        return "(not available: the session could not be read)"
    if "projects" in caller and caller["projects"] is None:  # a failed read is said, never `null` (review N5)
        caller = {**caller, "projects": "(could not be read)", "project_count": "(could not be read)"}
    return json.dumps(caller, indent=2, default=str)

LAST_ANSWER_CAP = 3000


def build_messages(config: ChatConfig, user_query: str, entity_dict: dict, plan_dict: dict,
                   pages: dict | None = None, session=None) -> list[dict]:
    """The system agent's context. The ``system`` blocks are the same for every question (the cached head:
    prompt, capabilities, endpoints, the catalog and docs indexes); the ``user`` blocks are this question's,
    CALLER among them: it differs per user, so it never goes in the cached head."""
    # Build full entity details for any resolved catalog codes using pre-built maps from config
    entity_details: dict = {}
    sampletypes_map = _as_map(getattr(config, "FULL_SAMPLETYPES_MAP", None))
    assays_map = _as_map(getattr(config, "FULL_ASSAYS_MAP", None))
    for st in entity_dict.get("sampletypes", []):
        code = st.get("code")
        if code and code in sampletypes_map:
            entity_details[code] = sampletypes_map[code]
    for assay in entity_dict.get("assays", []):
        code = assay.get("code")
        if code and code in assays_map:
            entity_details[code] = assays_map[code]
    # A project and an investigation may share a name (the real CSBC and MetNet do), so
    # they live in two maps and both are sent, the investigation under its own label.
    # Each row is sent as what chat_nextseek.context_rows says it is, whichever map it is in:
    # production's legacy project rows are typed 'investigation' with no parent_project and
    # are projects, never "<name> (investigation)"; a 'study' row is neither and is not sent.
    # A map that is not a dict (a MagicMock config in tests) reads as empty.
    projects_map = _as_map(getattr(config, "FULL_PROJECTS_MAP", None))
    investigations_map = _as_map(getattr(config, "FULL_INVESTIGATIONS_MAP", None))
    for project_name in entity_dict.get("projects", []):
        if not project_name:
            continue
        project = projects_map.get(project_name)
        if is_project_row(project):
            entity_details[project_name] = project
        investigation = investigations_map.get(project_name)
        if is_investigation_row(investigation):
            entity_details[f"{project_name} (investigation)"] = investigation

    entity_details.update(_lab_details(config, entity_dict, projects_map))

    if pages is None:
        pages = system_tools.load_docs(getattr(config, "DOCS_DIR", None))
    caps_doc = config.CAPABILITIES_DOC or "(No capabilities document loaded — describe general NExtSEEK capabilities.)"
    endpoints_json = json.dumps(config.MIN_API_ENDPOINTS, indent=2)
    # The graph agent's own rendering when the v1.1 catalog is live (structure, type index, the resolved types and
    # the question's vocabulary blocks); the committed JSON schema otherwise.
    catalog = live_catalog_context(config, user_query, entity_dict, plan_dict, reader="system agent")
    if catalog is not None:
        schema_json = catalog.schema + (f"\n{catalog.vocabulary}\n" if catalog.vocabulary else "")
    else:
        # The committed schema, without its vocabulary for a caller who is not an admin (graph_catalog).
        committed = graph_catalog.committed_schema(config)
        schema_json = json.dumps(committed, indent=2) if committed else "{}"
    entity_details_json = json.dumps(entity_details, indent=2) if entity_details else "{}"

    # This session's earlier turns and the full last reply (the log keeps only a 280-char preview), as user
    # blocks so the cached head is untouched. A fresh session adds nothing.
    memory = []
    history = history_block(session)
    if history:
        memory.append({"role": "user", "content": history})
        last = ((recent_turns(session, 1) or [{}])[-1].get("assistant_reply") or "")[:LAST_ANSWER_CAP]
        if last:
            memory.append({"role": "user", "content": f"LAST_ANSWER (your previous reply in full):\n{last}"})

    # The catalogs are an index here, not every row in full: get_catalog_entry and list_catalog read the rows.
    return [
        {"role": "system", "content": config.SYSTEM_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"CAPABILITIES_DOCUMENT:\n{caps_doc}"},
        {"role": "system", "content": f"ENDPOINT_CATALOG:\n{endpoints_json}"},
        {"role": "system", "content": f"CATALOG_INDEX:\n{system_tools.catalog_index(config)}"},
        {"role": "system", "content": f"DOCS_INDEX:\n{system_tools.docs_index(pages)}"},
        {"role": "user", "content": f"GRAPH_SCHEMA:\n{schema_json}"},
        {"role": "user", "content": f"ENTITY_RESULT (from entity agent):\n{json.dumps(entity_dict, indent=2)}"},
        {"role": "user", "content": f"ENTITY_DETAILS (full catalog data for resolved entities):\n{entity_details_json}"},
        {"role": "user", "content": f"CALLER:\n{_caller_text(config)}"},
        {"role": "user", "content": f"PARSER_INTENT:\n{json.dumps(plan_dict, indent=2)}"},
        *memory,
        {"role": "user", "content": f"QUESTION:\n{user_query}"},
    ]


def _run_tool(config, pages: dict, name: str, args: dict, read: set) -> dict:
    """One lookup. A page ``read_doc`` returned is added to ``read``: only those can be cited."""
    if name == "read_doc":
        out = system_tools.read_doc(pages, args.get("slug"), args.get("heading"))
        if out.get("ok"):
            read.add(out["slug"])
        return out
    if name == "get_catalog_entry":
        return system_tools.get_catalog_entry(config, args.get("kind"), args.get("key"))
    if name == "list_catalog":
        return system_tools.list_catalog(config, args.get("kind"), args.get("clade"), args.get("contains"))
    return {"ok": False, "error": f"unknown tool {name!r}"}


def _finish(args: dict, pages: dict, read: set, evidence: str | None = None) -> SystemAgentOutput:
    """The answer, with a link under it for every cited page that was read this turn (and no other).

    ``evidence`` (the turn's tool results, the question and CALLER) turns the number check on: a number found
    nowhere in it is removed and a short note added (U5.2). The loop has already given the model one retry."""
    narrative = str(args.get("narrative") or "").strip()
    if not narrative:
        raise RuntimeError("the answer tool gave an empty narrative")
    removed: list[str] = []
    if evidence is not None:
        removed = system_tools.unsupported_numbers(narrative, evidence)
        narrative = system_tools.drop_numbers(narrative, removed)
    footer, kept, dropped = system_tools.docs_footer(pages, args.get("docs_cited") or [], read, narrative)
    if footer:
        narrative = f"{narrative}\n\n{footer}"
    notes = str(args.get("notes") or "")
    if dropped:
        notes = (notes + "; " if notes else "") + f"dropped docs_cited not read this turn: {', '.join(dropped)}"
    if removed:
        notes = (notes + "; " if notes else "") + f"removed numbers no tool returned this turn: {', '.join(removed)}"
    mode = args.get("mode")
    if mode not in ("get_capabilities", "get_entities", "get_searches"):
        mode = "get_capabilities"
    consulted = [str(e) for e in args.get("entities_consulted") or [] if e]
    return SystemAgentOutput(mode=mode, narrative=narrative, entities_consulted=consulted,
                             docs_cited=kept, notes=notes)


def _failed(plan_dict: dict, error: str) -> SystemAgentOutput:
    """The canned answer for a turn that could not be answered."""
    return SystemAgentOutput(
        mode="get_capabilities",
        narrative=(
            f"I encountered an issue answering your question.\n\n"
            f"Parser intent: {plan_dict.get('intent_summary', '')}"
        ),
        entities_consulted=[],
        notes=f"error: {error}",
    )


def system_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: ParserPlan | dict,
    session=None,
) -> SystemAgentOutput:
    """
    Answer meta questions about the system: capabilities, catalog entity details, search options, and how to
    do things on the site (from the user docs). Looks the docs and the catalogs up with tools
    (``system_tools``), at most ``MAX_ITER`` times, then answers through the ``answer`` tool.
    Returns a SystemAgentOutput with a narrative answer ready for direct display.
    Falls back to a canned answer on failure; a model and fallback that both failed (LLMFatalError) end the turn.
    """
    print("\n[DEBUG][SYSTEM] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})

    pages = system_tools.load_docs(getattr(config, "DOCS_DIR", None))
    blocks = build_messages(config, user_query, entity_dict, plan_dict, pages, session)
    system = "\n\n".join(b["content"] for b in blocks if b["role"] == "system")
    messages: list[dict] = [
        {"role": "user", "content": "\n\n".join(b["content"] for b in blocks if b["role"] == "user")}
    ]
    read: set[str] = set()
    # What a number in the answer may come from: the tool results, the question, CALLER (U5.2) and ENTITY_DETAILS,
    # the catalog rows get_catalog_entry returns (review F7). The indexes and the schema are not evidence.
    caller_text = _caller_text(config)
    evidence: list[str] = [user_query, caller_text]
    evidence += [b["content"] for b in blocks if b["role"] == "user" and b["content"].startswith(("ENTITY_DETAILS", "CHAT_HISTORY", "LAST_ANSWER"))]
    retried = False

    sys_client, sys_model, sys_budget = config.get_agent_model(SYSTEM_AGENT_KEY)
    if not callable(getattr(sys_client, "chat_with_tools", None)):
        # No tool surface on this profile (as run_followup): nothing here can look anything up.
        return _failed(plan_dict, "the system agent's model has no tool surface")
    try:
        for iteration in range(MAX_ITER + 1):
            terminal = iteration == MAX_ITER
            if terminal:
                messages.append({"role": "user", "content": (
                    "This is your final turn and only `answer` is available. Answer now from what you "
                    "have read. Nothing else can run."
                )})
            resp = call_tools(
                config,
                # The last pass offers only `answer`; an always-thinking model would read the earlier
                # reasoning, written under other tools, as an edited history (followup.run_followup).
                messages=without_reasoning_blocks(messages) if terminal else messages,
                tools=system_tools.tool_schemas(final=terminal),
                system=system,
                model_name=sys_model,
                client=sys_client,
                agent_label=SYSTEM_AGENT_KEY,
                thinking_budget=sys_budget,
            )
            content = resp.get("content") or []
            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not tool_uses:
                # Prose without the answer tool: take it rather than spend another call.
                text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if text.strip():
                    text = system_tools.strip_unread_doc_links(text, read)
                    return _finish({"narrative": text, "notes": "answered without the answer tool"}, pages, read,
                                   "\n".join(evidence))
                break
            messages.append({"role": "assistant", "content": content})
            results = []
            for block in tool_uses:
                name, args = block.get("name"), block.get("input") or {}
                if name == "answer":
                    bad = system_tools.unsupported_numbers(str(args.get("narrative") or ""), "\n".join(evidence))
                    if bad and not retried and not terminal:
                        # One retry: the answer holds numbers no tool returned this turn.
                        retried = True
                        results.append({"type": "tool_result", "tool_use_id": block.get("id"),
                                        "content": json.dumps({"ok": False, "error": (
                                            f"The answer was not given. These numbers came from no tool result this turn: "
                                            f"{', '.join(bad)}. Call the tool that returns them (list_catalog for a count), "
                                            f"or leave them out, then call answer again.")})})
                        continue
                    result = _finish(args, pages, read, "\n".join(evidence))
                    print(f"[DEBUG][SYSTEM] mode={result.mode}, entities={result.entities_consulted}, "
                          f"docs={result.docs_cited}")
                    return result
                if terminal:
                    payload = {"ok": False, "error": f"`{name}` was not available on this turn and did not run."}
                else:
                    payload = _run_tool(config, pages, name, args, read)
                    evidence.append(json.dumps(payload, default=str))
                results.append({"type": "tool_result", "tool_use_id": block.get("id"),
                                "content": json.dumps(payload, default=str)})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"no answer after {MAX_ITER} lookups")
    except Exception as e:
        print(f"[DEBUG][SYSTEM] system_agent failed: {e!r}")
        return _failed(plan_dict, str(e))
