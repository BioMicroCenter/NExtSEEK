"""The graph agent's prompt and the parser's graph schema no longer say samples carry no metadata (spec 4.4, D14).

v1.1 writes every non-empty attribute of a sample onto its node, so the old lines ("exactly three properties",
"does NOT store", descriptive metadata "is NOT on the graph node") are false. They change; the routing preferences in
``min_graph_schema.json`` do not.
"""

import hashlib
import json
import re
from pathlib import Path

import chat_nextseek

PACKAGE = Path(chat_nextseek.__file__).resolve().parent
PROMPT = (PACKAGE / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")
MIN_SCHEMA_PATH = PACKAGE / "context" / "min_graph_schema.json"

# sha256 of json.dumps(<the dict below>, sort_keys=True, ensure_ascii=True), computed from min_graph_schema.json as
# it stood before this change (last touched in bd663c24):
#   {"disambiguation_rules_except_descriptive": every disambiguation rule but the descriptive-attribute one,
#    "graph_query_triggers": ..., "api_preferred_triggers": ...}
FROZEN_ROUTING_SHA256 = "bc8391287bc866bf1429c340177457713ef82acd6268fe05a8e6a68e8bedb0ef"
DESCRIPTIVE_RULE_PREFIX = "If the query filters or reports on a descriptive sample attribute"


def _descriptive_index(rules):
    matches = [i for i, rule in enumerate(rules) if rule.startswith(DESCRIPTIVE_RULE_PREFIX)]
    assert len(matches) == 1, "exactly one descriptive-attribute rule"
    return matches[0]


# --- graph_agent.txt ------------------------------------------------------------------------------------------------


def test_the_prompt_no_longer_says_samples_have_three_properties():
    assert "exactly three properties" not in PROMPT.lower()


def test_the_prompt_no_longer_says_the_graph_does_not_store_metadata():
    assert "does not store" not in PROMPT.lower()


def test_the_prompt_does_not_send_names_or_attributes_to_the_rest_api():
    lowered = PROMPT.lower()
    assert "rest api" not in lowered
    assert "exclusively" not in lowered
    assert not re.search(r"only (?:exist|live)s? .{0,40}\bapi\b", lowered)


def test_the_prompt_says_metadata_is_on_the_node_under_the_type_label():
    lowered = PROMPT.lower()
    assert "attribute title" in lowered
    assert "t_<code>" in lowered


def test_the_prompt_forbids_whole_sample_nodes_and_names_the_alternative():
    at = PROMPT.lower().find("whole sample node")
    assert at != -1, "a rule about whole Sample nodes"
    rule = PROMPT[at:at + 500]
    for alternative in ("s.id", "s.uuid", "s.type", "count(*)"):
        assert alternative in rule
    assert "never" in PROMPT[max(0, at - 80):at + 40].lower()


def test_the_prompt_says_the_label_and_sample_type_both_identify_the_type():
    assert "have no `code` property" not in PROMPT
    assert "SampleType.code" not in PROMPT.replace("`", "")
    assert re.search(r"Sample\.type.{0,200}T_", PROMPT, re.DOTALL)


def test_the_prompt_keeps_its_three_steps():
    for step in ("## STEP 1", "## STEP 2", "## STEP 3", "## Output format"):
        assert step in PROMPT


# --- min_graph_schema.json ------------------------------------------------------------------------------------------


def test_the_parser_schema_still_parses():
    data = json.loads(MIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert {"node_types", "relationships", "graph_query_triggers", "api_preferred_triggers",
            "disambiguation_rules"} <= set(data)


def test_the_sample_description_no_longer_says_metadata_is_off_the_node():
    data = json.loads(MIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    sample = next(n for n in data["node_types"] if n["label"] == "Sample")["description"]
    lowered = sample.lower()
    assert "not on the graph node" not in lowered
    assert "only identity" not in lowered
    assert "must be fetched there" not in lowered
    assert "attribute title" in lowered


def test_no_rule_gives_absent_graph_properties_as_a_reason():
    data = json.loads(MIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    for key in ("graph_query_triggers", "api_preferred_triggers", "disambiguation_rules"):
        for rule in data[key]:
            assert "do not exist on graph nodes" not in rule
            assert "can only match nothing" not in rule


def test_the_routing_rules_are_unchanged():
    data = json.loads(MIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    rules = data["disambiguation_rules"]
    descriptive = _descriptive_index(rules)
    frozen = {
        "disambiguation_rules_except_descriptive": [r for i, r in enumerate(rules) if i != descriptive],
        "graph_query_triggers": data["graph_query_triggers"],
        "api_preferred_triggers": data["api_preferred_triggers"],
    }
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    assert digest == FROZEN_ROUTING_SHA256
    assert descriptive == 7
    assert "→ API" in rules[descriptive]
