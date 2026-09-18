"""Prompt variants: an evaluation-only alternative set of Nessie prompts for one turn.

A variant is a directory ``prompts/variants/<name>/`` holding any of the files a turn's agents read, plus an
optional ``variant.json``. ``apply_variant(config, name)`` returns a SHALLOW copy of the ChatConfig whose
prompts and parser/API context files come from that directory, then from the directory it ``inherits``, then
from the defaults. The shared singleton is never mutated, so no other turn in the process sees the variant.

The request gate lives in ``NessieAI/cc/turn.py::_with_prompt_variant``: a superuser's
``QueryRequest.prompt_variant`` on a process that sets ``NEXTSEEK_EVAL_PARSER_FORCE=1``, the same gate as
``force_parser_mode``, and independent of it (an unforced turn with a variant is the main use).

Files a variant may hold (``PROMPT_FILES``, ``JSON_FILES``) and the config attribute each one replaces:

    graph_agent.txt                  GRAPH_AGENT_SYSTEM_PROMPT (then the addendum, if any, is appended)
    graph_schema_structure.txt       GRAPH_SCHEMA_STRUCTURE (graph_context.render_graph_context's structure)
    parser_core_routing.txt          PARSER_CORE_ROUTING_PROMPT, and both parser prompts are re-composed
    parser_agent.txt                 PARSER_SYSTEM_PROMPT (composed with the resolved routing core)
    multi_parser_agent.txt           MULTI_PARSER_SYSTEM_PROMPT (composed the same way)
    api_agent.txt                    API_AGENT_SYSTEM_PROMPT
    min_graph_schema.json            MIN_GRAPH_SCHEMA (the parser's view of the graph)
    min_api_endpoints_enriched.json  MIN_API_ENDPOINTS (the parser's and the API agent's endpoint catalog)

``variant.json`` (every key optional; a key a variant leaves out is inherited, else defaulted)::

    {"description": str, "inherits": "<variant name>" | null, "project_parser_plan": bool,
     "graph_agent_addendum": "<file in this variant's directory>" | null,
     "allowed_procedures": ["<fully.qualified.procedure>", ...]}

``project_parser_plan`` is proposal P5 (``agents/graph.py::project_parser_plan``). ``allowed_procedures``
extends ``cypher_text.ALLOWED_PROCEDURES`` for this variant's turns only (``EXTRA_ALLOWED_PROCEDURES``, read by
``helpers/tools/neo4j.py::tool_neo4j_query``); a procedure that writes, opens its own transactions, runs Cypher
from a string the text check cannot see, or does file, network or administrative work is refused here
(``DENIED_PROCEDURE_PREFIXES``), because the READ transaction does not cover every one of those.

Anything unexpected fails loudly (``VariantError``): an unknown name, an unexpected file or subdirectory, a
malformed ``variant.json``, a missing addendum, an inherits cycle, a routing core no wrapper would inject.
``validate_tree`` checks the whole committed tree and runs in the test suite. At runtime the gate catches the
error, logs it and runs the default prompts; the turn then records ``prompt_variant: null``, which the
harness preflight refuses.

Nothing here makes a model call or touches a database.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PARSER_CORE_PLACEHOLDER, compose_parser_prompt

#: The variant names a request may ask for. ``QueryRequest.prompt_variant``'s Literal and the harness's
#: ``--prompt-variant`` choices are pinned to this tuple by tests.
VARIANT_NAMES: tuple[str, ...] = ("v2", "v2_apoc")

VARIANTS_DIR: Path = Path(__file__).resolve().parent / "prompts" / "variants"
DEFAULT_PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"

MANIFEST = "variant.json"

#: prompt file -> the config attribute it replaces (the parser wrappers are composed, see ``_parser_prompts``)
_PROMPT_ATTRS: dict[str, str] = {
    "graph_agent.txt": "GRAPH_AGENT_SYSTEM_PROMPT",
    "graph_schema_structure.txt": "GRAPH_SCHEMA_STRUCTURE",
    "parser_core_routing.txt": "PARSER_CORE_ROUTING_PROMPT",
    "parser_agent.txt": "PARSER_SYSTEM_PROMPT",
    "multi_parser_agent.txt": "MULTI_PARSER_SYSTEM_PROMPT",
    "api_agent.txt": "API_AGENT_SYSTEM_PROMPT",
}
_JSON_ATTRS: dict[str, tuple[str, type]] = {
    "min_graph_schema.json": ("MIN_GRAPH_SCHEMA", dict),
    "min_api_endpoints_enriched.json": ("MIN_API_ENDPOINTS", list),
}
PROMPT_FILES: frozenset[str] = frozenset(_PROMPT_ATTRS)
JSON_FILES: frozenset[str] = frozenset(_JSON_ATTRS)
_PARSER_WRAPPERS = ("parser_agent.txt", "multi_parser_agent.txt")

_MANIFEST_KEYS = frozenset({"description", "inherits", "project_parser_plan", "graph_agent_addendum",
                            "allowed_procedures"})
_PROCEDURE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

#: Procedures no variant may allow, matched as case-insensitive prefixes. The text check is the first line and
#: the READ transaction the second; these are the ones the second line does not fully cover or that bypass the
#: first: Cypher run from a string (the mask blanks string literals, so an inner CALL is never checked), work in
#: transactions of their own, writes, file and network I/O, triggers and administration.
DENIED_PROCEDURE_PREFIXES: tuple[str, ...] = (
    "apoc.cypher.", "apoc.periodic.", "apoc.do.", "apoc.when", "apoc.case", "apoc.load.", "apoc.import.",
    "apoc.export.", "apoc.bolt.", "apoc.custom.", "apoc.trigger.", "apoc.systemdb.", "apoc.create.",
    "apoc.merge.", "apoc.refactor.", "apoc.atomic.", "apoc.lock.", "apoc.schema.assert", "apoc.nodes.delete",
    "apoc.util.sleep", "apoc.log.", "apoc.config.", "apoc.uuid.", "apoc.ttl.", "apoc.graph.fromCypher",
    "apoc.redis.", "apoc.mongo", "apoc.es.", "apoc.couchbase.", "apoc.warmup.", "apoc.convert.setJsonProperty",
    "apoc.nodes.link",
    "dbms.", "db.create", "db.drop", "db.clear", "tx.",
)


class VariantError(ValueError):
    """A prompt variant that cannot be loaded as written."""


@dataclass(frozen=True)
class PromptVariant:
    """One loaded variant: its resolved settings and the directories files are looked up in, nearest first."""

    name: str
    description: str
    inherits: str | None
    project_parser_plan: bool
    allowed_procedures: tuple[str, ...]
    addendum: Path | None
    chain: tuple[Path, ...] = field(default_factory=tuple)

    def resolve(self, filename: str) -> Path | None:
        """The nearest directory's copy of ``filename``, or None for the default."""
        for directory in self.chain:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        return None


def _root(variants_dir: Path | None) -> Path:
    return Path(variants_dir) if variants_dir is not None else VARIANTS_DIR


def _read_manifest(directory: Path, name: str) -> tuple[dict[str, Any], set[str]]:
    """The manifest's values and the keys it states, type-checked; {} and set() when there is no manifest."""
    path = directory / MANIFEST
    if not path.is_file():
        return {}, set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VariantError(f"prompt variant {name!r}: {MANIFEST} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise VariantError(f"prompt variant {name!r}: {MANIFEST} must be a JSON object")
    unknown = sorted(set(data) - _MANIFEST_KEYS)
    if unknown:
        raise VariantError(f"prompt variant {name!r}: {MANIFEST} has unknown key(s) {unknown}; "
                           f"the keys are {sorted(_MANIFEST_KEYS)}")
    checks = {
        "description": lambda v: isinstance(v, str),
        "inherits": lambda v: v is None or isinstance(v, str),
        "project_parser_plan": lambda v: isinstance(v, bool),
        "graph_agent_addendum": lambda v: v is None or isinstance(v, str),
        "allowed_procedures": lambda v: isinstance(v, list) and all(isinstance(p, str) for p in v),
    }
    for key, ok in checks.items():
        if key in data and not ok(data[key]):
            raise VariantError(f"prompt variant {name!r}: {MANIFEST} {key!r} has the wrong type "
                               f"({type(data[key]).__name__}: {data[key]!r})")
    return data, set(data)


def _check_procedures(name: str, procedures: list[str]) -> tuple[str, ...]:
    for proc in procedures:
        if not _PROCEDURE_NAME.match(proc):
            raise VariantError(f"prompt variant {name!r}: allowed_procedures entry {proc!r} is not a fully "
                               f"qualified procedure name (for example apoc.path.subgraphNodes)")
        lowered = proc.lower()
        denied = next((p for p in DENIED_PROCEDURE_PREFIXES if lowered.startswith(p.lower())), None)
        if denied:
            raise VariantError(f"prompt variant {name!r}: allowed_procedures entry {proc!r} is refused: "
                               f"{denied!r} procedures write, run Cypher from a string, open their own "
                               f"transactions, or do file, network or administrative work")
    return tuple(dict.fromkeys(procedures))


def _check_addendum(name: str, directory: Path, addendum: str) -> Path:
    if not addendum or addendum in (".", "..") or "/" in addendum or "\\" in addendum:
        raise VariantError(f"prompt variant {name!r}: graph_agent_addendum {addendum!r} must be a plain file "
                           f"name in the variant's own directory")
    if addendum in PROMPT_FILES or addendum in JSON_FILES or addendum == MANIFEST:
        raise VariantError(f"prompt variant {name!r}: graph_agent_addendum {addendum!r} names a file the "
                           f"variant loader already reads; give the addendum a name of its own")
    path = directory / addendum
    if not path.is_file():
        raise VariantError(f"prompt variant {name!r}: graph_agent_addendum {addendum!r} does not exist in "
                           f"{directory}")
    return path


def _check_contents(name: str, directory: Path, addendum: str | None) -> None:
    allowed = PROMPT_FILES | JSON_FILES | {MANIFEST} | ({addendum} if addendum else set())
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            raise VariantError(f"prompt variant {name!r}: unexpected subdirectory {entry.name!r}")
        if entry.name not in allowed:
            raise VariantError(f"prompt variant {name!r}: unexpected file {entry.name!r}; a variant holds only "
                               f"{sorted(PROMPT_FILES | JSON_FILES)}, {MANIFEST} and its declared addendum")


def load_variant(name: str, *, variants_dir: Path | None = None, _seen: tuple[str, ...] = ()) -> PromptVariant:
    """Load and validate variant ``name`` and everything it inherits. Raises ``VariantError``."""
    if name not in VARIANT_NAMES:
        raise VariantError(f"unknown prompt variant {name!r}; the variants are {list(VARIANT_NAMES)}")
    if name in _seen:
        raise VariantError(f"prompt variant inherits cycle: {' -> '.join((*_seen, name))}")
    root = _root(variants_dir)
    directory = root / name
    if not directory.is_dir():
        raise VariantError(f"prompt variant {name!r} has no directory at {directory}")

    data, stated = _read_manifest(directory, name)
    addendum_name = data.get("graph_agent_addendum")
    _check_contents(name, directory, addendum_name)

    parent = None
    inherits = data.get("inherits")
    if inherits is not None:
        if inherits == name:
            raise VariantError(f"prompt variant {name!r} inherits itself")
        if inherits not in VARIANT_NAMES:
            raise VariantError(f"prompt variant {name!r} inherits unknown prompt variant {inherits!r}; "
                               f"the variants are {list(VARIANT_NAMES)}")
        parent = load_variant(inherits, variants_dir=root, _seen=(*_seen, name))

    project = data["project_parser_plan"] if "project_parser_plan" in stated else (
        parent.project_parser_plan if parent else False)
    procedures = _check_procedures(name, data["allowed_procedures"]) if "allowed_procedures" in stated else (
        parent.allowed_procedures if parent else ())
    if "graph_agent_addendum" in stated:
        addendum = _check_addendum(name, directory, addendum_name) if addendum_name is not None else None
    else:
        addendum = parent.addendum if parent else None

    variant = PromptVariant(
        name=name,
        description=data.get("description", ""),
        inherits=inherits,
        project_parser_plan=project,
        allowed_procedures=procedures,
        addendum=addendum,
        chain=(directory, *(parent.chain if parent else ())),
    )
    _check_core_is_injected(variant)
    return variant


def _default_wrapper(filename: str, prompts_dir: Path) -> str:
    return (prompts_dir / filename).read_text(encoding="utf-8")


def _check_core_is_injected(variant: PromptVariant, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> None:
    """A variant's routing core must reach at least one parser prompt, or the file is dead weight."""
    if variant.resolve("parser_core_routing.txt") is None:
        return
    for wrapper in _PARSER_WRAPPERS:
        path = variant.resolve(wrapper) or (prompts_dir / wrapper)
        if path.is_file() and PARSER_CORE_PLACEHOLDER in path.read_text(encoding="utf-8"):
            return
    raise VariantError(f"prompt variant {variant.name!r}: parser_core_routing.txt is never injected: neither "
                       f"parser wrapper it resolves carries {PARSER_CORE_PLACEHOLDER}")


def validate_tree(*, variants_dir: Path | None = None) -> dict[str, PromptVariant]:
    """Load every variant directory under ``variants_dir``; an unknown directory or a stray file fails."""
    root = _root(variants_dir)
    if not root.is_dir():
        return {}
    loaded = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            raise VariantError(f"unexpected file {entry.name!r} in {root}; it holds only variant directories "
                               f"named {list(VARIANT_NAMES)}")
        loaded[entry.name] = load_variant(entry.name, variants_dir=root)
    return loaded


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path, expected: type, name: str):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VariantError(f"prompt variant {name!r}: {path.name} is not valid JSON ({exc})") from exc
    if not isinstance(data, expected):
        raise VariantError(f"prompt variant {name!r}: {path.name} must hold a JSON "
                           f"{'object' if expected is dict else 'array'}, not {type(data).__name__}")
    return data


def apply_variant(config, name: str, *, variants_dir: Path | None = None):
    """A shallow copy of ``config`` running variant ``name``. Raises ``VariantError``; never mutates ``config``.

    Sets on the copy, besides the replaced prompts and context files: ``PROMPT_VARIANT`` (the name),
    ``PROMPT_VARIANT_FILES`` ({file: "<variant>/<file>"} for every file a variant directory supplied, the
    addendum as ``graph_agent_addendum``), ``PROJECT_PARSER_PLAN`` and ``EXTRA_ALLOWED_PROCEDURES``.
    """
    root = _root(variants_dir)
    variant = load_variant(name, variants_dir=root)
    prompts_dir = Path(getattr(config, "PROMPTS_DIR", None) or DEFAULT_PROMPTS_DIR)
    out = copy.copy(config)
    files: dict[str, str] = {}

    def text(filename: str) -> str | None:
        path = variant.resolve(filename)
        if path is None:
            return None
        files[filename] = _relative(path, root)
        return path.read_text(encoding="utf-8")

    for filename in ("graph_agent.txt", "api_agent.txt"):
        value = text(filename)
        if value is not None:
            setattr(out, _PROMPT_ATTRS[filename], value)
    structure = text("graph_schema_structure.txt")
    if structure is not None:
        out.GRAPH_SCHEMA_STRUCTURE = structure.strip()

    core = text("parser_core_routing.txt")
    if core is not None:
        out.PARSER_CORE_ROUTING_PROMPT = core
    for wrapper_name in _PARSER_WRAPPERS:
        wrapper = text(wrapper_name)
        if wrapper is None and core is None:
            continue  # neither half changed: the composed default stands
        raw = wrapper if wrapper is not None else _default_wrapper(wrapper_name, prompts_dir)
        setattr(out, _PROMPT_ATTRS[wrapper_name], compose_parser_prompt(raw, out.PARSER_CORE_ROUTING_PROMPT))

    if variant.addendum is not None:
        files["graph_agent_addendum"] = _relative(variant.addendum, root)
        addendum = variant.addendum.read_text(encoding="utf-8")
        out.GRAPH_AGENT_SYSTEM_PROMPT = out.GRAPH_AGENT_SYSTEM_PROMPT.rstrip("\n") + "\n\n" + addendum

    for filename, (attr, expected) in _JSON_ATTRS.items():
        path = variant.resolve(filename)
        if path is None:
            continue
        setattr(out, attr, _read_json(path, expected, name))
        files[filename] = _relative(path, root)
    if "min_api_endpoints_enriched.json" in files and getattr(out, "ENDPOINT_INDEX", None) is not None:
        # The semantic index shortlists entries of the DEFAULT catalog; the variant's parser sees its own whole.
        # Methods and body schemas (CATALOG_ENDPOINT_METHODS, API_SCHEMA) describe the real API and stay shared.
        out.ENDPOINT_INDEX = None

    out.PROMPT_VARIANT = variant.name
    out.PROMPT_VARIANT_FILES = dict(sorted(files.items()))
    out.PROJECT_PARSER_PLAN = variant.project_parser_plan
    out.EXTRA_ALLOWED_PROCEDURES = frozenset(variant.allowed_procedures)
    return out


def variant_record(config) -> dict[str, Any]:
    """What a turn's debug payload records: the variant that ran and the files it supplied, or two Nones.

    Type-checked, not just read: tests hand the orchestrator a MagicMock config, whose every attribute exists.
    """
    name = getattr(config, "PROMPT_VARIANT", None)
    if not isinstance(name, str) or name not in VARIANT_NAMES:
        return {"prompt_variant": None, "prompt_variant_files": None}
    files = getattr(config, "PROMPT_VARIANT_FILES", None)
    return {"prompt_variant": name, "prompt_variant_files": dict(files) if isinstance(files, dict) else None}
