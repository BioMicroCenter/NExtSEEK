"""Plan 005 Task 11: independent oracles for generated route_capabilities.json."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from build_tools.gen_op_surfaces.constants import (
    BAKED_CAPABILITIES_REL,
    CANONICAL_CAPABILITIES_REL,
    ROUTE_CAPABILITIES_REL,
)
from build_tools.gen_op_surfaces.route_capabilities import (
    build_route_capabilities_payload,
    render_route_capabilities_bytes,
)
from dmac_assistant.router.capabilities import load_capabilities
from nessie_tests import corpus as nessie_corpus
from nessie_tests import export as nexport
from nessie_tests import runner as nessie_runner
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.ns_capabilities import (
    STALE_PIPELINE_PHRASE,
    NsCapabilitiesError,
    project_ns_capabilities,
)
from nextseek_api.cc_assistant.op_registry.ops import OPS
from nextseek_api.cc_assistant.op_registry.paired_evidence import (
    FORCED_IMAGE_BY_ARM,
    arm_success,
    load_committed_evidence,
)
from nextseek_api.cc_assistant.op_registry.routes import (
    GENERIC_CC_BUILTINS,
    NEXTSEEK_QUERY_TOOLS,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_MD = REPO_ROOT / CANONICAL_CAPABILITIES_REL
BAKED_MD = REPO_ROOT / BAKED_CAPABILITIES_REL
ROUTE_JSON = REPO_ROOT / ROUTE_CAPABILITIES_REL
EVIDENCE_PATH = (
    REPO_ROOT / "nextseek_api/cc_assistant/op_registry/route_example_evidence.json"
)
CORPUS_PATH = REPO_ROOT / "nessie_tests/corpus.json"
PLUGIN_JSON = (
    REPO_ROOT
    / "docker/cc-runtime/build_context/plugins/nextseek/.claude-plugin/plugin.json"
)
DOCKERFILE = REPO_ROOT / "docker/cc-runtime/Dockerfile"
PLUGINS_ROOT = REPO_ROOT / "docker/cc-runtime/build_context/plugins"
ROUTER_BAML = REPO_ROOT / "dmac_assistant/baml_src/router.baml"
NS_ROUTE = "nextseek_query"
CC_ROUTE = "container_cc"
MAX_EXAMPLES = 2

UNSELECTED_BODY_SNIPPETS = (
    "no plotting or rendering capability",
    STALE_PIPELINE_PHRASE,
    "Organ on Chip",
    "Be specific about sample type",
)


def _success_csv(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _arm_ok(outcome: dict[str, Any], *, arm: str) -> bool:
    return arm_success(
        {
            "image": outcome["image"],
            "answer_provided": "true" if outcome["answer_provided"] else "false",
            "is_error": "true" if outcome["is_error"] else "false",
            "timed_out": "true" if outcome["timed_out"] else "false",
            "runtime_success": "true" if outcome["runtime_success"] else "false",
            "human_success": _success_csv(outcome.get("human_success")),
            "llm_success": _success_csv(outcome.get("llm_success")),
        },
        forced_image=FORCED_IMAGE_BY_ARM[arm],
    )


def _refresh_success(evidence: dict[str, Any]) -> None:
    for record in evidence["records"]:
        record["ns"]["success"] = _arm_ok(record["ns"], arm="ns")
        record["cc"]["success"] = _arm_ok(record["cc"], arm="cc")


def _independent_rank_key(query_id: str, usefulness: float | None) -> tuple[int, float, str]:
    if usefulness is None:
        return (1, 0.0, query_id)
    return (0, -float(usefulness), query_id)


def _independent_top_queries(
    records: list[dict[str, Any]], *, route: str, family: str
) -> list[str]:
    arm = "ns" if route == NS_ROUTE else "cc"
    ranked: list[tuple[tuple[int, float, str], str, str]] = []
    for record in records:
        if record["task_family"] != family:
            continue
        if not _arm_ok(record[arm], arm=arm):
            continue
        ranked.append(
            (
                _independent_rank_key(record["query_id"], record[arm].get("usefulness_score")),
                record["query_id"],
                record["query_text"],
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    seen: set[str] = set()
    for _, _, text in ranked:
        if text in seen:
            continue
        out.append(text)
        seen.add(text)
        if len(out) >= MAX_EXAMPLES:
            break
    return out


def _independent_family_projection(
    evidence: dict[str, Any], descriptions: dict[str, str], *, route: str
) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in evidence["records"]:
        name = record["task_family"]
        if name in seen:
            continue
        examples = _independent_top_queries(
            evidence["records"], route=route, family=name
        )
        if not examples:
            continue
        families.append(
            {
                "name": name,
                "description": descriptions[name],
                "example_queries": examples,
            }
        )
        seen.add(name)
    return families


def _corpus_family_descriptions(path: Path = CORPUS_PATH) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: body["description"].strip()
        for name, body in payload["families"].items()
        if isinstance(body, dict) and isinstance(body.get("description"), str)
    }


def _independent_unfenced_lines(text: str) -> list[str]:
    lines: list[str] = []
    fence = None
    opener = re.compile(r"^(```|~~~)")
    for raw in text.split("\n"):
        match = opener.match(raw)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            lines.append(raw)
            continue
        if match and match.group(1) == fence:
            fence = None
    if fence is not None:
        raise AssertionError("independent oracle: unclosed fence")
    return lines


def _independent_ns_projection(markdown: str) -> dict[str, Any]:
    lines = _independent_unfenced_lines(markdown)
    h2_at: dict[str, int] = {}
    h2_order: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith("## ") and not line.startswith("### "):
            title = line[3:].strip()
            h2_order.append(title)
            h2_at[title] = index
    required = ["Overview", "What You Can Ask", "What the System Cannot Do"]
    positions = [h2_order.index(name) for name in required]
    assert positions == sorted(positions)
    def _until(name: str) -> list[str]:
        start = h2_at[name] + 1
        later = [h2_at[title] for title in h2_at if h2_at[title] > h2_at[name]]
        end = min(later) if later else len(lines)
        return lines[start:end]
    overview_lines = []
    for line in _until("Overview"):
        if not line.strip():
            if overview_lines:
                break
            continue
        overview_lines.append(line.strip())
    description = re.sub(r"\s+", " ", " ".join(overview_lines)).strip()
    tools: list[str] = []
    seen_keys: set[str] = set()
    for line in _until("What You Can Ask"):
        if not line.startswith("### "):
            continue
        label = re.sub(r"^\d+\.\s+", "", line[4:].strip()).strip()
        key = unicodedata.normalize("NFKC", label).casefold()
        assert key not in seen_keys
        seen_keys.add(key)
        tools.append(label)
    negatives: list[str] = []
    seen_neg: set[str] = set()
    lead_re = re.compile(r"^- \*\*(.+?)\*\*")
    for line in _until("What the System Cannot Do"):
        match = lead_re.match(line)
        if not match:
            continue
        lead = match.group(1).strip()
        key = unicodedata.normalize("NFKC", lead).casefold()
        assert key not in seen_neg
        seen_neg.add(key)
        negatives.append(lead)
    return {
        "description": description,
        "tools": tools,
        "best_for": (
            "Requests supported by the NS capability authority: "
            + "; ".join(tools)
            + "."
        ),
        "not_for": "Not intended for: " + "; ".join(negatives) + ".",
        "negative_labels": negatives,
    }


def _independent_container_tools() -> list[str]:
    discovery = discover_install(plugins_root=PLUGINS_ROOT, dockerfile_path=DOCKERFILE)
    installed = {shim.shim_name for shim in discovery.shims}
    tools: list[str] = []
    seen: set[str] = set()
    for op in OPS:
        if op.available and op.bin_name in installed and op.bin_name not in seen:
            tools.append(op.bin_name)
            seen.add(op.bin_name)
    for builtin in GENERIC_CC_BUILTINS:
        assert builtin not in seen
        tools.append(builtin)
        seen.add(builtin)
    return tools


def _routable_from_baml() -> set[str]:
    text = ROUTER_BAML.read_text(encoding="utf-8")
    aliases = set(re.findall(r'@alias\("([^"]+)"\)', text.split("enum Route {", 1)[1].split("}", 1)[0]))
    return aliases - {"unrelated"}


def _caps_from_file(path: Path = ROUTE_JSON):
    return load_capabilities(path)


def _route_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {route["route_name"]: route for route in payload["routes"]}


def _family_index(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {family["name"]: family for family in route["task_families"]}


def test_baked_capabilities_bytes_equal_canonical() -> None:
    assert BAKED_MD.read_bytes() == CANONICAL_MD.read_bytes()


def test_registry_loads_through_real_loader_and_is_nonempty() -> None:
    caps = _caps_from_file()
    assert caps
    names = [item.route_name for item in caps]
    assert names == [NS_ROUTE, CC_ROUTE]


def test_every_route_name_is_a_real_routable_route() -> None:
    routable = _routable_from_baml()
    for cap in _caps_from_file():
        assert cap.route_name in routable


def test_no_duplicate_route_names() -> None:
    names = [cap.route_name for cap in _caps_from_file()]
    assert len(names) == len(set(names))


def test_task_families_are_well_formed() -> None:
    for cap in _caps_from_file():
        assert cap.task_families
        for family in cap.task_families:
            assert family.name.strip()
            assert family.description.strip()
            assert family.example_queries
            assert len(family.example_queries) <= MAX_EXAMPLES


def test_ns_fields_match_independent_markdown_oracle() -> None:
    markdown = CANONICAL_MD.read_text(encoding="utf-8")
    expected = _independent_ns_projection(markdown)
    produced = project_ns_capabilities(markdown)
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    ns = _route_map(payload)[NS_ROUTE]
    assert ns["description"] == expected["description"] == produced.description
    assert ns["best_for"] == expected["best_for"] == produced.best_for
    assert ns["not_for"] == expected["not_for"] == produced.not_for
    assert list(produced.tools) == expected["tools"]
    for label in expected["tools"]:
        assert label in ns["best_for"]
        assert label not in ns["tools"]
    assert STALE_PIPELINE_PHRASE not in json.dumps(ns)


def test_ns_tools_keep_fallback_router_agent_vocabulary() -> None:
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    ns = _route_map(payload)[NS_ROUTE]
    assert ns["tools"] == list(NEXTSEEK_QUERY_TOOLS)
    baml = (REPO_ROOT / "dmac_assistant/baml_src/router.baml").read_text(encoding="utf-8")
    assert "class RouteCapability" in baml
    assert "tools          string[]" in baml
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "show",
            "a9d69522:dmac_assistant/build_context/route_capabilities.json",
        ],
        text=True,
    )
    pinned = json.loads(raw)
    pinned_ns = next(route for route in pinned["routes"] if route["route_name"] == NS_ROUTE)
    assert ns["tools"] == pinned_ns["tools"]


def test_container_tools_match_independent_install_and_ops_oracle() -> None:
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    cc = _route_map(payload)[CC_ROUTE]
    expected = _independent_container_tools()
    assert cc["tools"] == expected
    assert len(cc["tools"]) == len(set(cc["tools"]))
    plugin_text = PLUGIN_JSON.read_text(encoding="utf-8")
    for tool in cc["tools"]:
        if tool in GENERIC_CC_BUILTINS:
            continue
        assert tool not in plugin_text


def test_full_route_family_projection_matches_independent_oracle() -> None:
    evidence = load_committed_evidence(EVIDENCE_PATH)
    descriptions = _corpus_family_descriptions()
    fingerprint = nessie_runner.corpus_fingerprint(CORPUS_PATH)
    assert evidence["corpus_fingerprint"] == fingerprint
    variants = {item.id: item for item in nessie_corpus.load_all_definitions(CORPUS_PATH)}
    for record in evidence["records"]:
        variant = variants[record["query_id"]]
        assert record["task_family"] == variant.family
        assert record["query_text"] == nexport.query_text(variant)
        assert record["corpus_fingerprint"] == fingerprint
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    for route_name in (NS_ROUTE, CC_ROUTE):
        expected = _independent_family_projection(
            evidence, descriptions, route=route_name
        )
        actual = _route_map(payload)[route_name]["task_families"]
        assert [item["name"] for item in actual] == [item["name"] for item in expected]
        assert actual == expected
        for family in actual:
            assert len(family["example_queries"]) <= MAX_EXAMPLES


def test_neither_success_queries_are_absent() -> None:
    evidence = load_committed_evidence(EVIDENCE_PATH)
    neither = set(evidence["audit"]["neither_success"])
    neither_text = {
        record["query_text"]
        for record in evidence["records"]
        if record["query_id"] in neither
    }
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    for route in payload["routes"]:
        for family in route["task_families"]:
            overlap = set(family["example_queries"]) & neither_text
            assert not overlap


def test_negative_prompt_oracle_rejects_unselected_markdown_and_stale_phrase() -> None:
    payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
    ns = _route_map(payload)[NS_ROUTE]
    route_level = json.dumps(
        {k: ns[k] for k in ("description", "tools", "best_for", "not_for")},
        ensure_ascii=False,
    )
    for snippet in UNSELECTED_BODY_SNIPPETS:
        assert snippet not in route_level
    assert STALE_PIPELINE_PHRASE not in json.dumps(payload, ensure_ascii=False)
    markdown = CANONICAL_MD.read_text(encoding="utf-8")
    poisoned = markdown.replace("### 1. Sample Search", "### 1. Sample Search cannot run pipelines")
    with pytest.raises(NsCapabilitiesError, match="maintainer review"):
        project_ns_capabilities(poisoned)
    poisoned_lead = markdown.replace(
        "**Generate visualizations or charts.**",
        "**Generate visualizations that cannot run pipelines.**",
    )
    with pytest.raises(NsCapabilitiesError, match="maintainer review"):
        project_ns_capabilities(poisoned_lead)


def test_malformed_duplicate_fenced_and_unicode_labels_fail() -> None:
    base = (
        "## Overview\n\nHello world.\n\n"
        "## What You Can Ask\n\n### Alpha\n\n### Beta\n\n"
        "## What the System Cannot Do\n\n- **No writes** body.\n"
    )
    project_ns_capabilities(base)
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(base.replace("### Beta", "### ALPHA"))
    colliding = base.replace("### Beta", "### \uff21lpha")
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(colliding)
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(base + "\n```\nunclosed")
    nested = base.replace("### Beta", "#### Nested")
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(nested)
    fenced_heading = (
        "## Overview\n\nHello world.\n\n"
        "## What You Can Ask\n\n```\n### Fabricated\n```\n### Alpha\n\n"
        "## What the System Cannot Do\n\n- **No writes** body.\n"
    )
    projected = project_ns_capabilities(fenced_heading)
    assert "Fabricated" not in projected.tools
    missing = base.replace("## What You Can Ask\n\n### Alpha\n\n### Beta\n\n", "## What You Can Ask\n\n")
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(missing)
    swapped = (
        "## Overview\n\nHello world.\n\n"
        "## What the System Cannot Do\n\n- **No writes** body.\n\n"
        "## What You Can Ask\n\n### Alpha\n"
    )
    with pytest.raises(NsCapabilitiesError):
        project_ns_capabilities(swapped)
    over_label = "X" * 121
    with pytest.raises(NsCapabilitiesError, match="maintainer review"):
        project_ns_capabilities(base.replace("### Alpha", f"### {over_label}"))
    huge = "word " * 800
    with pytest.raises(NsCapabilitiesError, match="maintainer review"):
        project_ns_capabilities(base.replace("Hello world.", huge))


def test_arm_and_grade_mutations_change_or_fail_expected_output() -> None:
    evidence = load_committed_evidence(EVIDENCE_PATH)
    original = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=evidence)
    original_ns = [f["name"] for f in _route_map(original)[NS_ROUTE]["task_families"]]
    swapped = copy.deepcopy(evidence)
    for record in swapped["records"]:
        if _arm_ok(record["ns"], arm="ns") == _arm_ok(record["cc"], arm="cc"):
            continue
        record["ns"], record["cc"] = record["cc"], record["ns"]
        record["ns"]["image"] = FORCED_IMAGE_BY_ARM["ns"]
        record["cc"]["image"] = FORCED_IMAGE_BY_ARM["cc"]
    _refresh_success(swapped)
    swapped_payload = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=swapped)
    assert swapped_payload != original

    graded = copy.deepcopy(evidence)
    ns_example = _route_map(original)[NS_ROUTE]["task_families"][0]
    winner = next(
        record
        for record in graded["records"]
        if record["task_family"] == ns_example["name"]
        and record["query_text"] == ns_example["example_queries"][0]
    )
    winner["ns"]["human_success"] = False
    _refresh_success(graded)
    graded_payload = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=graded)
    assert graded_payload != original

    useful = copy.deepcopy(evidence)
    family = useful["records"][0]["task_family"]
    members = [
        record
        for record in useful["records"]
        if record["task_family"] == family and _arm_ok(record["ns"], arm="ns")
    ]
    assert len(members) >= 3
    for record in members:
        record["ns"]["usefulness_score"] = 0.0
    members[-1]["ns"]["usefulness_score"] = 0.91
    members[-2]["ns"]["usefulness_score"] = 0.90
    _refresh_success(useful)
    useful_payload = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=useful)
    ns_family = _family_index(_route_map(useful_payload)[NS_ROUTE])[family]
    assert ns_family["example_queries"] == [
        members[-1]["query_text"],
        members[-2]["query_text"],
    ]

    dropped = copy.deepcopy(evidence)
    del dropped["records"][0]["cc"]
    with pytest.raises(Exception):
        build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=dropped)

    missing = copy.deepcopy(evidence)
    missing["records"] = missing["records"][1:]
    with pytest.raises(Exception):
        from nextseek_api.cc_assistant.op_registry.paired_evidence import (
            validate_committed_structure,
        )

        validate_committed_structure(missing)
        build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=missing)

    duplicate = copy.deepcopy(evidence)
    duplicate["records"] = [duplicate["records"][0], duplicate["records"][0]]
    with pytest.raises(Exception):
        from nextseek_api.cc_assistant.op_registry.paired_evidence import (
            validate_committed_structure,
        )

        validate_committed_structure(duplicate)

    stale_text = copy.deepcopy(evidence)
    stale_text["records"][0]["query_text"] = "fabricated example that is not in the corpus"
    with pytest.raises(Exception):
        build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=stale_text)

    assert original_ns  # families existed before mutations


def test_third_example_is_not_emitted_and_top_two_win() -> None:
    evidence = load_committed_evidence(EVIDENCE_PATH)
    family = evidence["records"][0]["task_family"]
    ns_ok = [
        record
        for record in evidence["records"]
        if record["task_family"] == family and _arm_ok(record["ns"], arm="ns")
    ]
    if len(ns_ok) < 3:
        pytest.fail("need at least three NS-success records in one family to rank top-two")
    for index, record in enumerate(ns_ok[:3]):
        record["ns"]["usefulness_score"] = float(3 - index)
    _refresh_success(evidence)
    expected = _independent_top_queries(evidence["records"], route=NS_ROUTE, family=family)
    assert len(expected) == 2
    payload = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=evidence)
    actual = _family_index(_route_map(payload)[NS_ROUTE])[family]["example_queries"]
    assert actual == expected
    assert ns_ok[2]["query_text"] not in actual


def test_route_policy_and_plugin_json_do_not_move_families() -> None:
    evidence = load_committed_evidence(EVIDENCE_PATH)
    baseline = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=evidence)
    baseline_families = {
        route: [family["name"] for family in body["task_families"]]
        for route, body in _route_map(baseline).items()
    }
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    corpus["route_policy"]["families"]["sample_search"]["value"] = "container_cc"
    tmp_corpus = Path("/tmp") / f"plan005-corpus-mut-{Path.cwd().name}.json"
    tmp_corpus.write_text(json.dumps(corpus), encoding="utf-8")
    mutated_evidence = copy.deepcopy(evidence)
    mutated_evidence["corpus_fingerprint"] = nessie_runner.corpus_fingerprint(tmp_corpus)
    for record in mutated_evidence["records"]:
        record["corpus_fingerprint"] = mutated_evidence["corpus_fingerprint"]
    mutated = build_route_capabilities_payload(
        repo_root=REPO_ROOT,
        evidence=mutated_evidence,
        corpus_path=tmp_corpus,
    )
    mutated_families = {
        route: [family["name"] for family in body["task_families"]]
        for route, body in _route_map(mutated).items()
    }
    assert mutated_families == baseline_families
    original_plugin = PLUGIN_JSON.read_text(encoding="utf-8")
    try:
        plugin = json.loads(original_plugin)
        plugin["description"] = "mutated plugin prose must not become a tool"
        PLUGIN_JSON.write_text(json.dumps(plugin), encoding="utf-8")
        after = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=evidence)
        after_families = {
            route: [family["name"] for family in body["task_families"]]
            for route, body in _route_map(after).items()
        }
        assert after_families == baseline_families
        assert "mutated plugin prose" not in json.dumps(after)
    finally:
        PLUGIN_JSON.write_text(original_plugin, encoding="utf-8")

    flipped = copy.deepcopy(evidence)
    record = flipped["records"][0]
    record["ns"]["human_success"] = not bool(record["ns"]["human_success"])
    record["ns"]["runtime_success"] = True
    record["ns"]["answer_provided"] = True
    record["ns"]["is_error"] = False
    record["ns"]["timed_out"] = False
    _refresh_success(flipped)
    flipped_payload = build_route_capabilities_payload(repo_root=REPO_ROOT, evidence=flipped)
    flipped_families = {
        route: [family["name"] for family in body["task_families"]]
        for route, body in _route_map(flipped_payload).items()
    }
    assert flipped_families != baseline_families or flipped_payload != baseline


def test_prompt_file_has_no_forbidden_placeholders() -> None:
    rendered = ROUTE_JSON.read_text(encoding="utf-8")
    for phrase in (
        "fallback decides",
        "family-to-route map",
        "posterior default",
        STALE_PIPELINE_PHRASE,
    ):
        assert phrase not in rendered
    source = (
        REPO_ROOT / "build_tools/gen_op_surfaces/route_capabilities.py"
    ).read_text(encoding="utf-8")
    assert "sha256" not in source.lower() or "F-10" not in source
    tests = Path(__file__).read_text(encoding="utf-8")
    assert "route_capabilities.json" in tests
    pins = (
        REPO_ROOT / "nextseek_api/cc_assistant/tests/test_f_constraint_pins.py"
    ).read_text(encoding="utf-8")
    assert "test_route_capabilities_unmodified" in pins
    assert "was deleted" in pins.lower() or "deleted here" in pins


def test_whole_file_render_is_byte_stable_and_loader_clean() -> None:
    rendered = render_route_capabilities_bytes(REPO_ROOT)
    assert ROUTE_JSON.read_bytes() == rendered
    loaded = load_capabilities(ROUTE_JSON)
    assert {item.route_name for item in loaded} == {NS_ROUTE, CC_ROUTE}


def test_no_f10_hash_pin_restored() -> None:
    digest = hashlib.sha256(ROUTE_JSON.read_bytes()).hexdigest()
    search_roots = [
        REPO_ROOT / "nextseek_api/assistant/tests/test_route_capabilities.py",
        REPO_ROOT / "nextseek_api/cc_assistant/tests/test_f_constraint_pins.py",
        REPO_ROOT / "nextseek_api/cc_assistant/tests/test_baml_router_schema.py",
        REPO_ROOT / "build_tools/gen_op_surfaces/route_capabilities.py",
    ]
    for path in search_roots:
        text = path.read_text(encoding="utf-8")
        assert digest not in text
        if path.name == "test_route_capabilities.py":
            assert "F-10" not in text or "no_f10" in text
