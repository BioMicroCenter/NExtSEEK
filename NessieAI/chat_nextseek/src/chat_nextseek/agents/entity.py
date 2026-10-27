from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ..session import SessionState

from ..config import ChatConfig
from ..graph_context import STUDY_WORDS_RE
from ..llm_clients import LLMAPIConnectionError, LLMRateLimitError, LLMTimeoutError
from ..helpers import (
    log_usage,
    safe_parse_json,
)
from ..helpers.lab_code import resolve_labs
from ..schemas.schema_helper import StructuredOutputError, call_llm_structured, call_llm_text, empty_output_problem
from ..schemas import (
    EntityAgentOutput,
)
from ..schemas.entity import EntityItem, LabMatch, LabNearMiss


#: Only the -ic/-ics/-ome forms name a category of data (transcriptomic, proteome). An -ing word names a process
#: (sequencing, imaging, staining) that many unrelated kinds of data go through, so it expands nothing.
_CATEGORY_SUFFIX = re.compile(r"(ics|ic|omes|ome)$")
#: A word that names more types than this is not a category of data ("samples"), so it expands nothing.
# ponytail: fixed cap; read it from the catalog's size if the catalog grows a lot.
_CATEGORY_MAX_TYPES = 15


def expand_category_types(query: str, result: EntityAgentOutput, catalog: list[dict] | None) -> None:
    """A word for a kind of data covers every type of that kind (fix 2).

    The catalog's own Tags place a type in a category: "transcriptomic" is a tag word of the expression,
    single-cell, spatial and RNA types. When the question uses such a word and the entity step kept at least one of
    its types, the others are added, and ``category_types`` records word -> codes so the reply can name them. A
    question that names one type by its code or name keeps that one type.
    """
    q = (query or "").lower()
    chosen = {i.code for i in result.sampletypes}
    for word in dict.fromkeys(re.findall(r"[a-z]{5,}", q)):
        stem = _CATEGORY_SUFFIX.sub("", word)
        # Only a category form (transcriptomic, proteome): a plain noun like "tissue" names a type, not a category.
        if stem == word or len(stem) < 4:
            continue
        members = [r for r in catalog or [] if any(
            w.startswith(stem) for w in re.findall(r"[a-z]+", str(r.get("Tags", "")).lower()))]
        codes = [r["SampleType"] for r in members if r.get("SampleType")]
        if not 2 <= len(codes) <= _CATEGORY_MAX_TYPES or not chosen & set(codes) or set(codes) <= chosen:
            continue
        if any(re.search(rf"\b{re.escape(str(r.get('SampleType', '')).lower())}\b", q)
               or str(r.get("Name", "")).lower() in q for r in members):
            continue
        for r in members:
            if r.get("SampleType") and r["SampleType"] not in chosen:
                result.sampletypes.append(EntityItem(code=r["SampleType"], name=r.get("Name")))
                chosen.add(r["SampleType"])
        result.category_types[word] = codes


#: The house conventions of entity_agent.txt ("DOMAIN CONVENTIONS"), applied in code: (words a question uses, code).
#: The prompt block lists the same words; test_entity_house_types.py keeps the two in step.
HOUSE_SAMPLE_TYPES: list[tuple[tuple[str, ...], str]] = [
    (("PBMC", "peripheral blood mononuclear cell"), "TIS"),
    (("BAL", "bronchoalveolar lavage"), "TIS"),
    (("T cell", "NK cell", "natural killer cell", "macrophage", "monocyte", "dendritic cell", "neutrophil",
      "B cell"), "CEL"),
]


#: A quoted span: double or curly quotes, or single quotes that are not apostrophes (no letter just outside them).
_QUOTED_RE = re.compile(r'"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)\'[^\']*\'(?!\w)')


def apply_house_sample_types(query: str, result: EntityAgentOutput, catalog: list[dict] | None) -> None:
    """Add the house sample type of a word the question uses when the entity agent left it out.

    The list's word is also kept as a keyword unless the agent already has it or the question's form of it, in any
    letter case. A code the catalog does not hold is never added; with no catalog the code is added with no name.
    Each word found is recorded in ``result.house_types`` with its code, whoever put the code in ``sampletypes``.
    The field is code-owned: whatever the model wrote there is cleared first, also when the question is left alone.
    Words match whole, any letter case, a space or a hyphen between their parts, with an optional plural s.
    A word inside a quoted span counts for nothing, and a question that names a study or paper (STUDY_WORDS_RE)
    is left to the agent: there the word is most likely part of a title, not the kind of sample asked for.
    """
    result.house_types = {}
    if STUDY_WORDS_RE.search(query or ""):
        return
    text = _QUOTED_RE.sub(" ", query or "")
    names = {r.get("SampleType"): r.get("Name") for r in catalog or [] if isinstance(r, dict)}
    chosen = {i.code for i in result.sampletypes}
    have = {k.lower() for k in result.keywords}
    for words, code in HOUSE_SAMPLE_TYPES:
        for word in words:
            parts = re.escape(word).replace(r"\ ", r"[\s-]")
            m = re.search(rf"(?<![\w-]){parts}s?(?![\w-])", text, re.IGNORECASE)
            if not m:
                continue
            if code not in chosen and (code in names or not names):
                result.sampletypes.append(EntityItem(code=code, name=names.get(code)))
                chosen.add(code)
            if code in chosen:
                result.house_types[word] = code
            if word.lower() not in have and m.group(0).lower() not in have:
                result.keywords.append(word)
                have.add(word.lower())


def carry_code_owned_types(plan: Any, entity_result: Any) -> Any:
    """``plan`` with the entity result's code-written ``house_types`` and ``category_types`` in ``plan.resolved``.

    The graph agent reads the parser plan's resolved block, and the parser's echo of the entity result leaves both
    out. ``house_types`` always comes from the entity result, empty when it has none, so a map the parser model wrote
    is dropped. A category map is restored when the entity step found one, with any of its codes the echo left out of
    ``sampletypes``. The NS clamp and the graph op both call it; the lab codes stay the clamp's alone. Anything that
    is not a parser plan (no ``EntityAgentOutput`` as ``resolved``) is returned as it is.
    """
    if not isinstance(getattr(plan, "resolved", None), EntityAgentOutput):
        return plan

    def _get(name):
        return entity_result.get(name) if isinstance(entity_result, dict) else getattr(entity_result, name, None)

    update: dict[str, Any] = {"house_types": dict(_get("house_types") or {})}
    categories = _get("category_types") or {}
    if categories:
        update["category_types"] = {w: list(codes) for w, codes in categories.items()}
        names = {}
        for item in _get("sampletypes") or []:
            item = item if isinstance(item, dict) else item.model_dump()
            names[item.get("code")] = item.get("name")
        types = list(plan.resolved.sampletypes)
        have = {t.code for t in types}
        for code in (c for codes in categories.values() for c in codes):
            if code not in have:
                types.append(EntityItem(code=code, name=names.get(code)))
                have.add(code)
        update["sampletypes"] = types
    return plan.model_copy(update={"resolved": plan.resolved.model_copy(update=update)})


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _resolve_lab_names(
    config: ChatConfig,
    user_query: str,
    result: EntityAgentOutput,
    sampletypes: Any,
    assays: Any,
    projects: Any,
) -> None:
    """Resolve the LLM's lab and person names against SEEK's lab records, in place.

    ``labs`` becomes the matched labs' surnames as SEEK spells them, ``lab_codes`` only
    matched codes, ``lab_matches`` what matched and how, and ``scientists`` gains person
    names that matched no lab (also appended to ``keywords``). ``config.LABS`` that is not
    a list means no labs document: ``labs`` then passes through unresolved, with no codes.
    Spec: docs/superpowers/specs/2026-09-18-projects-labs-context.md section 7.
    """
    records = getattr(config, "LABS", None)
    # M5 reads the whole catalogs the config holds; a caller's shortlist is only a fallback.
    catalog_sampletypes = getattr(config, "MIN_SAMPLETYPES", None)
    catalog_assays = getattr(config, "MIN_ASSAYS", None)
    catalogs = (
        (catalog_sampletypes if isinstance(catalog_sampletypes, list) else _as_list(sampletypes))
        + (catalog_assays if isinstance(catalog_assays, list) else _as_list(assays))
    )
    try:
        resolution = resolve_labs(
            user_query,
            result.labs,
            records=records,
            llm_scientists=result.scientists,
            llm_keywords=result.keywords,
            catalogs=catalogs,
            projects=_as_list(projects),
        )
    except Exception as exc:  # a matcher bug must not fail the turn, nor guess a code
        print(f"[WARN][ENTITY] Lab resolution failed, labs left unresolved: {exc!r}")
        result.lab_codes = []
        result.lab_matches = []
        result.lab_near_misses = []
        return
    if not resolution.available:
        print(
            "[WARN][ENTITY] No lab records (config.LABS is "
            f"{type(records).__name__}): labs passed through unresolved, no lab codes."
        )
    result.labs = resolution.labs
    result.lab_codes = resolution.lab_codes
    result.lab_matches = [LabMatch(**match) for match in resolution.lab_matches]
    result.lab_near_misses = [LabNearMiss(**miss) for miss in resolution.near_misses]
    result.scientists = resolution.scientists
    result.keywords = resolution.keywords


def entity_agent(
    config: ChatConfig,
    user_query: str,
    sampletypes: list[dict] | None = None,
    assays: list[dict] | None = None,
    projects: list[dict] | None = None,
) -> EntityAgentOutput:
    """
    Run the Entity agent to resolve sampletypes, assays, and keywords from a user query.
    Accepts optional pre-shortlisted catalogs to reduce prompt size and falls back to min catalogs when absent.
    Returns a validated `EntityAgentOutput`, degrading gracefully when structured parsing fails.
    """
    print("\n[DEBUG][ENTITY] User query:", user_query)

    sampletypes = sampletypes if sampletypes is not None else config.MIN_SAMPLETYPES
    assays = assays if assays is not None else config.MIN_ASSAYS
    projects = projects if projects is not None else config.MIN_PROJECTS

    sampletypes_json = json.dumps(sampletypes, indent=2) if sampletypes else "[]"
    assays_json = json.dumps(assays, indent=2) if assays else "[]"
    projects_json = json.dumps(projects, indent=2) if projects else "[]"

    messages = [
        {"role": "system", "content": config.ENTITY_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "SAMPLE TYPES CATALOG (JSON array from min_sampletypes.json):\n" + sampletypes_json,
        },
        {
            "role": "system",
            "content": "ASSAYS CATALOG (JSON array from min_assays.json):\n" + assays_json,
        },
        {
            "role": "system",
            "content": "PROJECTS CATALOG (JSON array from projects_db.json):\n" + projects_json,
        },
        {"role": "user", "content": user_query},
    ]

    entity_client, entity_model, entity_budget = config.get_agent_model("entity")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=EntityAgentOutput,
            system=config.ENTITY_SYSTEM_PROMPT,
            messages=messages,
            model_name=entity_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="entity",
            log_payload_extra={"user_query": user_query},
            usage_label="ENTITY",
            thinking_budget=entity_budget,
            client=entity_client,
            result_check=empty_output_problem,
        )
    except Exception as e:
        print("[DEBUG][ENTITY] Exception or parse error (structured):", repr(e))
        # A timeout has had the ladder's one second chance already: the move to the
        # fallback model, or with no chain a retry on a fresh socket. There is no third
        # call (operator ruling 2026-09-28): it went to the model that had just stalled,
        # and with both models stalled it made the entity take 780 s. When both models
        # fail the ladder raises LLMFatalError, which ends the turn with the plain text.
        if isinstance(e, LLMTimeoutError):
            result = EntityAgentOutput()
        else:
            # Fallback: raw call without forced response_format. Through the recovery
            # ladder, so a timeout, a 503 or an empty body moves to the entity's next
            # provider; it used to call the SDK directly and never moved. It runs on the
            # entity's wall clocks (call_budgets.CALL_BUDGETS), like its structured call.
            try:
                raw_content = call_llm_text(
                    config,
                    messages=messages,
                    model_name=entity_model,
                    client=entity_client,
                    agent_label="entity",
                    log_label="entity_raw",
                    temperature=0,
                    thinking_budget=entity_budget,
                    usage_label="ENTITY_FALLBACK",
                )
                parsed = safe_parse_json(raw_content)
                if isinstance(parsed, list):
                    parsed = {"sampletypes": parsed, "assays": [], "keywords": []}
                if not isinstance(parsed, dict):
                    parsed = {}
                result = EntityAgentOutput.model_validate(parsed)
            except Exception as e_fallback:
                print("[DEBUG][ENTITY] Fallback exception:", repr(e_fallback))
                result = EntityAgentOutput()

    _resolve_lab_names(config, user_query, result, sampletypes, assays, projects)
    apply_house_sample_types(user_query, result, getattr(config, "MIN_SAMPLETYPES", None))
    expand_category_types(user_query, result, getattr(config, "MIN_SAMPLETYPES", None))

    print("[DEBUG][ENTITY] Parsed entity result:", json.dumps(result.model_dump(), indent=2))
    return result
