"""Whole-file generation for dmac_assistant/build_context/route_capabilities.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.ns_capabilities import (
    STALE_PIPELINE_PHRASE,
    load_ns_projection,
)
from nextseek_api.cc_assistant.op_registry.ops import OPS
from nextseek_api.cc_assistant.op_registry.paired_evidence import (
    FORCED_IMAGE_BY_ARM,
    PairedEvidenceError,
    arm_success,
    load_committed_evidence,
    validate_committed_structure,
)
from nextseek_api.cc_assistant.op_registry.routes import (
    CONTAINER_CC_ROUTE,
    GENERIC_CC_BUILTINS,
)
from nessie_tests import corpus as nessie_corpus
from nessie_tests import export as nexport
from nessie_tests import runner as nessie_runner

ROUTE_CAPABILITIES_REL = Path("dmac_assistant") / "build_context" / "route_capabilities.json"
CANONICAL_CAPABILITIES_REL = Path(
    "chat_nextseek/src/chat_nextseek/context/capabilities.md"
)
BAKED_CAPABILITIES_REL = Path(
    "docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md"
)
EVIDENCE_REL = Path("nextseek_api/cc_assistant/op_registry/route_example_evidence.json")
CORPUS_REL = Path("nessie_tests/corpus.json")
PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")

NS_ROUTE = "nextseek_query"
CC_ROUTE = "container_cc"
MAX_EXAMPLES_PER_FAMILY = 2

FORBIDDEN_PROMPT_PHRASES = (
    STALE_PIPELINE_PHRASE,
    "fallback decides",
    "family-to-route map",
    "posterior default",
)


class RouteCapabilitiesError(ValueError):
    """Raised when route_capabilities.json cannot be generated honestly."""


def _success_to_csv(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def recompute_arm_success(outcome: dict[str, Any], *, arm: str) -> bool:
    return arm_success(
        {
            "image": outcome["image"],
            "answer_provided": "true" if outcome["answer_provided"] else "false",
            "is_error": "true" if outcome["is_error"] else "false",
            "timed_out": "true" if outcome["timed_out"] else "false",
            "runtime_success": "true" if outcome["runtime_success"] else "false",
            "human_success": _success_to_csv(outcome.get("human_success")),
            "llm_success": _success_to_csv(outcome.get("llm_success")),
        },
        forced_image=FORCED_IMAGE_BY_ARM[arm],
    )


def _rank_tuple(query_id: str, usefulness: float | None) -> tuple[int, float, str]:
    if usefulness is None:
        return (1, 0.0, query_id)
    return (0, -float(usefulness), query_id)


def select_family_examples(
    records: list[dict[str, Any]],
    *,
    route: str,
    family: str,
) -> list[str]:
    arm = "ns" if route == NS_ROUTE else "cc"
    candidates: list[tuple[tuple[int, float, str], str]] = []
    for record in records:
        if record["task_family"] != family:
            continue
        if not recompute_arm_success(record[arm], arm=arm):
            continue
        key = _rank_tuple(record["query_id"], record[arm].get("usefulness_score"))
        candidates.append((key, record["query_text"]))
    candidates.sort(key=lambda item: item[0])
    selected: list[str] = []
    seen: set[str] = set()
    for _, query_text in candidates:
        if query_text in seen:
            continue
        selected.append(query_text)
        seen.add(query_text)
        if len(selected) >= MAX_EXAMPLES_PER_FAMILY:
            break
    return selected


def _family_descriptions(corpus_path: Path) -> dict[str, str]:
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    families = payload.get("families")
    if not isinstance(families, dict):
        raise RouteCapabilitiesError("corpus families block missing")
    out: dict[str, str] = {}
    for name, body in families.items():
        if name.startswith("_") or not isinstance(body, dict):
            continue
        description = body.get("description")
        if isinstance(description, str) and description.strip():
            out[name] = description.strip()
    return out


def _corpus_index(corpus_path: Path) -> dict[str, dict[str, str]]:
    variants = nessie_corpus.load_all_definitions(corpus_path)
    index: dict[str, dict[str, str]] = {}
    for variant in variants:
        if variant.id in index:
            raise RouteCapabilitiesError(f"duplicate corpus id {variant.id!r}")
        index[variant.id] = {
            "family": variant.family,
            "query_text": nexport.query_text(variant),
        }
    return index


def _validate_evidence_against_corpus(
    evidence: dict[str, Any],
    *,
    corpus_path: Path,
) -> None:
    fingerprint = nessie_runner.corpus_fingerprint(corpus_path)
    if evidence.get("corpus_fingerprint") != fingerprint:
        raise RouteCapabilitiesError(
            "evidence corpus_fingerprint is stale versus current corpus.json"
        )
    index = _corpus_index(corpus_path)
    for record in evidence["records"]:
        query_id = record["query_id"]
        if query_id not in index:
            raise RouteCapabilitiesError(f"evidence id {query_id!r} missing from corpus")
        expected = index[query_id]
        if record["task_family"] != expected["family"]:
            raise RouteCapabilitiesError(f"family drift for {query_id!r}")
        if record["query_text"] != expected["query_text"]:
            raise RouteCapabilitiesError(f"query_text drift for {query_id!r}")
        if record.get("corpus_fingerprint") not in (None, fingerprint):
            raise RouteCapabilitiesError(f"per-record fingerprint drift for {query_id!r}")


def container_cc_tools(*, repo_root: Path) -> list[str]:
    discovery = discover_install(
        plugins_root=repo_root / PLUGINS_ROOT_REL,
        dockerfile_path=repo_root / DOCKERFILE_REL,
    )
    installed = {shim.shim_name for shim in discovery.shims}
    bins: list[str] = []
    seen: set[str] = set()
    for op in OPS:
        if not op.available or op.bin_name not in installed:
            continue
        if op.bin_name in seen:
            raise RouteCapabilitiesError(f"duplicate container tool {op.bin_name!r}")
        bins.append(op.bin_name)
        seen.add(op.bin_name)
    for builtin in GENERIC_CC_BUILTINS:
        if builtin in seen:
            raise RouteCapabilitiesError(f"duplicate container tool {builtin!r}")
        bins.append(builtin)
        seen.add(builtin)
    return bins


def _task_families_for_route(
    *,
    route: str,
    evidence: dict[str, Any],
    descriptions: dict[str, str],
) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in evidence["records"]:
        name = record["task_family"]
        if name in seen:
            continue
        examples = select_family_examples(evidence["records"], route=route, family=name)
        if not examples:
            continue
        description = descriptions.get(name)
        if not description:
            raise RouteCapabilitiesError(
                f"corpus has no description for evidenced family {name!r}"
            )
        families.append(
            {
                "name": name,
                "description": description,
                "example_queries": examples,
            }
        )
        seen.add(name)
    return families


def build_route_capabilities_payload(
    *,
    repo_root: Path,
    evidence: dict[str, Any] | None = None,
    corpus_path: Path | None = None,
    markdown_path: Path | None = None,
) -> dict[str, Any]:
    corpus = corpus_path or (repo_root / CORPUS_REL)
    md_path = markdown_path or (repo_root / CANONICAL_CAPABILITIES_REL)
    baked = repo_root / BAKED_CAPABILITIES_REL
    canonical_bytes = md_path.read_bytes()
    if baked.is_file() and baked.read_bytes() != canonical_bytes:
        raise RouteCapabilitiesError("baked capabilities.md is not byte-identical to canonical")

    projection = load_ns_projection(md_path)
    payload = evidence
    if payload is None:
        evidence_path = repo_root / EVIDENCE_REL
        payload = load_committed_evidence(evidence_path)
    try:
        validate_committed_structure(payload)
    except PairedEvidenceError as exc:
        raise RouteCapabilitiesError(str(exc)) from exc
    _validate_evidence_against_corpus(payload, corpus_path=corpus)
    descriptions = _family_descriptions(corpus)
    ns_families = _task_families_for_route(
        route=NS_ROUTE, evidence=payload, descriptions=descriptions
    )
    cc_families = _task_families_for_route(
        route=CC_ROUTE, evidence=payload, descriptions=descriptions
    )
    ns_route = {
        **projection.route_level_object(),
        "task_families": ns_families,
    }
    cc_tools = container_cc_tools(repo_root=repo_root)
    cc_route = {
        "route_name": CONTAINER_CC_ROUTE.route_name,
        "description": CONTAINER_CC_ROUTE.description,
        "tools": cc_tools,
        "best_for": CONTAINER_CC_ROUTE.best_for,
        "not_for": CONTAINER_CC_ROUTE.not_for,
        "task_families": cc_families,
    }
    if len(cc_tools) != len(set(cc_tools)):
        raise RouteCapabilitiesError("container_cc.tools contains duplicates")
    if len(projection.tools) != len(set(projection.tools)):
        raise RouteCapabilitiesError("nextseek_query.tools contains duplicates")
    document = {"routes": [ns_route, cc_route]}
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    for phrase in FORBIDDEN_PROMPT_PHRASES:
        if phrase in rendered:
            raise RouteCapabilitiesError(
                f"forbidden prompt phrase {phrase!r} entered route_capabilities.json"
            )
    return document


def render_route_capabilities_bytes(repo_root: Path) -> bytes:
    document = build_route_capabilities_payload(repo_root=repo_root)
    rendered = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _load_through_real_loader(rendered)
    return rendered


def _load_through_real_loader(rendered: bytes) -> None:
    import os
    import tempfile

    from dmac_assistant.router.capabilities import load_capabilities

    tmp_dir = os.environ.get("TMPDIR") or "/tmp"
    with tempfile.NamedTemporaryFile(
        prefix="route-capabilities-",
        suffix=".json",
        dir=tmp_dir,
        delete=True,
    ) as handle:
        handle.write(rendered)
        handle.flush()
        loaded = load_capabilities(Path(handle.name))
    if not loaded:
        raise RouteCapabilitiesError("real load_capabilities returned no routes")


def emit_route_capabilities(repo_root: Path) -> bytes:
    return render_route_capabilities_bytes(repo_root)
