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
# Re-pinned once, reviewed: the "Investigation titles to recognize" rule gained TCGA (not on every instance), as the
# generated capabilities block and spec 2026-09-18-projects-labs-context.md section 13.3 have it. No other rule or
# trigger changed (was bc8391287bc866bf1429c340177457713ef82acd6268fe05a8e6a68e8bedb0ef).
#
# Re-pinned again, reviewed, by F1: this file is now the measured one, and the routing it carries IS the change.
# Read before re-pinning a third time -- a moving hash that nobody diffs is not a guard.
#   disambiguation rules 10 -> 12. Four rewritten from "prefer API" to graph_query (an unscoped "how many X" is a
#     count of samples; a sampletype + assay keyword filter; parents with children of several types). Two new: a
#     plain keyword or attribute search with no scope is graph_query, and a person's name resolves to the lab code
#     inside UIDs for a lab or PI and to the Scientist attribute for anyone else, never the people endpoint.
#   graph_query triggers 10 -> 16, all six additions metadata shapes REST used to take: typed numeric and date
#     comparisons, conditions that must all hold, counts and breakdowns and spelling variants, lab and person scope,
#     and a count across every type.
#   api_preferred triggers 5 -> 4: the three sample-search ones go, replaced by catalog records, the full record or
#     an export by UID, and "any NON-METADATA intent that maps cleanly to a known endpoint".
#
# Re-pinned a third time, reviewed: the export rule names the download API by its new path, samples/retrieve
# (admin/samples/retrieve is its deprecated alias). Checked by replacing that one string in the old frozen JSON,
# which then equals the new one exactly; no rule or trigger changed (was c7d01c88e6d5...f908c47).
FROZEN_ROUTING_SHA256 = "7273608d61fa36647eece09140690b0f630261fd6659da8bedaf6cccebb3673a"
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
    # The prohibition is the last bullet of the "return the answer" section and the alternatives are
    # the bullets above it, so the window spans the section rather than only what follows the rule.
    rule = PROMPT[max(0, at - 2500):at + 500]
    for alternative in ("s.id", "s.uuid", "s.type", "count(*)"):
        assert alternative in rule, alternative
    assert "never" in PROMPT[max(0, at - 80):at + 40].lower()


def test_the_prompt_says_the_label_and_sample_type_both_identify_the_type():
    assert "have no `code` property" not in PROMPT
    assert "SampleType.code" not in PROMPT.replace("`", "")
    # The promoted prompt states it the other way round: the label first, then the code s.type holds.
    assert re.search(r"T_<code>.{0,400}`s\.type` holds the code", PROMPT, re.DOTALL)


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
    # F1: a descriptive attribute is a property of the Sample node, so the rule that used to send it
    # to advanced_search now sends it to the graph. This is the rule the promotion exists to change;
    # it is excluded from the frozen hash above precisely because it moves.
    assert "→ graph_query" in rules[descriptive]


def test_a_collection_date_question_reads_the_uid_date():
    """Operator ruling 2026-09-23 (supersedes the SampleCreationDate reading of 2026-09-22): "collection
    dates" means the YYMMDD in TYPE-YYMMDDLAB-n, which every sample carries. The local run asked for the
    longest span of collection dates and the agent used CollectionDate (134 samples)."""
    assert "**Which date.**" in PROMPT
    rule = PROMPT.split("**Which date.**", 1)[1].split("\n", 1)[0]
    assert "TYPE-YYMMDDLAB-n" in rule and "split(s.uuid, '-')[1]" in rule
    assert "only when the user names that attribute" in rule
    assert "s.CollectionDate STARTS WITH" not in PROMPT
