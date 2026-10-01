"""Protocol routing and the graph-schema text, as the operator approved on 2026-09-24.

SOP == protocol. The graph holds each sample's Protocol value (a node property) and the SOP it names
(protocol_title on DERIVED_FROM), but no SOP records. So "which protocols did samples use", in any scope,
is graph_query; the list of SOPs on file stays a catalog record (new_search); SOPs registered to a
project stay on the reporter. The schema-text fixes (F1-F6) come from the schema chat's read-only review
of the live graph (schema 1.2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek import graph_context as gc

PACKAGE = Path(chat_nextseek.__file__).resolve().parent
CORE = (PACKAGE / "prompts" / "parser_core_routing.txt").read_text(encoding="utf-8")
SCHEMA = json.loads((PACKAGE / "context" / "min_graph_schema.json").read_text(encoding="utf-8"))

ASSAY_TEST = "(r.internal_assay_title = $assay OR $assay IN coalesce(r.internal_assay_titles, []))"


def _node(label):
    return next(n for n in SCHEMA["node_types"] if n["label"] == label)["description"]


def _rel(kind):
    return next(r for r in SCHEMA["relationships"] if r["type"] == kind)["note"]


# --- protocols (A, D, E, F, G) ---------------------------------------------------------------------------------

def test_protocols_samples_used_are_graph_query_and_the_graph_holds_no_sop_records():
    assert "Questions about the protocols (SOPs) that samples used are sample metadata" in CORE
    assert "The graph holds no SOP" in CORE


def test_the_reporter_keeps_only_sops_registered_to_a_project():
    assert "SOPs registered to a project:" in CORE
    assert "The reporter counts the SOP records filed under a project." in CORE


def test_protocol_usage_is_no_longer_a_reporter_signal():
    for stale in ("Protocol usage summary", "protocol usage summary", "protocols used/registered",
                  '"show me protocols for CGR"'):
        assert stale not in CORE, stale


def test_the_reporter_exclusions_and_step_six_send_protocol_usage_to_the_graph():
    assert ("- User asks which protocols or SOPs samples used, in any scope (a project, investigation, study, "
            "lab, sample type or UID), or how many samples followed one; use graph_query.") in CORE
    assert "- Is the user asking which protocols or SOPs samples used, in any scope" in CORE
    assert '("protocol summary for SRP" is graph_query; go on to step 6)' in CORE


def test_the_catalog_record_line_is_unchanged():
    """Edit B was dropped by the operator: the SOP list stays a catalog record in the original words."""
    assert ("- The user wants catalog records themselves (assays, sample types, SOPs, projects or\n"
            "  investigations as records), registered user accounts, or files; use new_search with that\n"
            "  endpoint.") in CORE


@pytest.mark.parametrize("question, fires", [
    ("What SOPs do the MetNet samples follow?", True),
    ("which sop was used", True),
    ("the desktop layout", False),
    ("Is this a soprano?", False),
])
def test_the_protocol_gate_hears_sop(question, fires):
    assert gc.mentions(gc.PROTOCOL_WORDS, question) is fires


def test_sop_is_a_plain_word_like_protocol():
    assert "sop" in gc._PLAIN_WORDS and "protocol" in gc._PLAIN_WORDS


# --- schema text (F1-F6) ---------------------------------------------------------------------------------------

def test_f1_studies_are_paper_level_or_seek_studies():
    study = _node("Study")
    assert "IS a published paper" not in study
    assert "IS NOT NULL" in study and "coalesce(st.DOI,'') <> ''" in study
    assert "a SEEK study, which carries neither key" in study
    assert "it is true for every study" not in study


def test_f2_the_known_investigation_titles_include_the_live_ones():
    for title in ("Griffith", "Impact", "RMS-NGC", "SRP", "Shoulders"):
        assert title in _node("Investigation"), title
        rule = next(r for r in SCHEMA["disambiguation_rules"] if r.startswith("Investigation titles to recognize"))
        assert title in rule, title
        line = next(l for l in CORE.splitlines() if l.startswith("Known investigation titles:"))
        assert title in line, title
    assert "MyTestInvestigation" not in CORE


def test_f3_derived_from_names_the_plural_assay_list_and_the_approved_test():
    note = _rel("DERIVED_FROM")
    assert "internal_assay_titles" in note
    assert ASSAY_TEST in note


def test_f4_a_project_is_its_own_node():
    note = _rel("IN_INVESTIGATION")
    assert "investigation/project" not in note
    assert "A project is its own node, reached from a sample by IN_PROJECT." in note


def test_f5_projects_containing_a_type_is_one_hop():
    assert "requires traversal from Sample to Study to Investigation" not in CORE
    assert '"Which projects contain samples of type T?" is one hop, from Sample to Project through IN_PROJECT.' in CORE


def test_f6_the_parser_no_longer_says_every_study_is_a_paper():
    assert "in this instance a study IS a published paper" not in CORE
    assert "a paper-level study is named after its paper; the rest are SEEK studies with their own titles" in CORE
