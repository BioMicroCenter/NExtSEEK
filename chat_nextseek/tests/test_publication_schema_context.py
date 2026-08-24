"""The routing schema must describe the study publication attributes.

The graph agent writes Cypher from this file. A property that exists in Neo4j
but not here is a property the agent will never query — and a property named
with the wrong casing returns null rather than raising.
"""

import json
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "src" / "chat_nextseek" / "context"


def _min_schema():
    return json.loads((CONTEXT / "min_graph_schema.json").read_text())


def test_study_description_names_doi_and_pmid_with_real_casing():
    # The live properties are uppercase; a lowercase name silently returns null
    # for every study.
    study = next(n for n in _min_schema()["node_types"] if n["label"] == "Study")
    assert "DOI" in study["description"]
    assert "PMID" in study["description"]


def test_study_description_warns_the_sentinel_is_empty_string():
    study = next(n for n in _min_schema()["node_types"] if n["label"] == "Study")
    text = study["description"]
    assert "empty string" in text or "''" in text


def test_no_publication_node_was_introduced():
    # This revision deliberately has no Publication label.
    labels = [n["label"] for n in _min_schema()["node_types"]]
    assert "Publication" not in labels


def test_triggers_cover_both_directions():
    triggers = " ".join(_min_schema()["graph_query_triggers"]).lower()
    assert "doi" in triggers
    assert "pmid" in triggers or "pubmed" in triggers
    assert "paper" in triggers or "publication" in triggers


def test_generated_schema_files_are_not_hand_edited():
    for name in ("neo4j_schema.json", "neo4j_schema_dev.json", "neo4j_schema_prod.json"):
        assert "fetched_at" in json.loads((CONTEXT / name).read_text())
