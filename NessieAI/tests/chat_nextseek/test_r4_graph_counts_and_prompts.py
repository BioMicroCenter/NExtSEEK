"""Round 4, unit A: distinct sample counts (U2.4), the rewrite limits (U2.7) and the graph prompt edits (U1).
Entities are made up."""
from pathlib import Path

import pytest

from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1, sample_ids
from chat_nextseek.helpers.tools import neo4j as neo4j_tool

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"
AGENT = (PROMPTS / "graph_agent.txt").read_text(encoding="utf-8")
STRUCTURE = (PROMPTS / "graph_schema_structure.txt").read_text(encoding="utf-8")


# ---- U2.4 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("column", ["uuid", "sample_id"])
def test_sample_ids_reads_the_column_every_row_holds(column):
    rows = [{column: "a", "study": "S1"}, {column: "a", "study": "S2"}, {column: "b", "study": "S1"}]
    assert sample_ids(rows) == ["a", "a", "b"]


@pytest.mark.parametrize("rows", [[], [{"n": 3}], [{"uuid": "a"}, {"uuid": None}], [{"uuid": "a"}, {"x": 1}],
                                  [{"uuid": True}], ["x"]])
def test_rows_without_a_sample_id_in_every_row_say_nothing(rows):
    assert sample_ids(rows) is None


def _review(rows, total=None):
    return review_tier1(
        ReviewInput(question="Which samples are in the Zeta papers?", cypher="MATCH (s:Sample) RETURN s.uuid AS uuid",
                    parameters={}, keyword_fields={}, rows=rows, count=len(rows), total=total, ok=True, error=None),
        DictCatalog({}))


def test_the_reviewer_flags_more_rows_than_distinct_samples():
    rows = [{"uuid": "ZZZ-1", "study": "S1"}, {"uuid": "ZZZ-1", "study": "S2"}, {"uuid": "ZZZ-2", "study": "S1"}]
    review = _review(rows, total=2)
    assert "duplicate_rows" in [c.name for c in review.checks if c.fired]
    assert "3 rows for 2 distinct samples" in review.disclosure


def test_the_reviewer_is_quiet_when_rows_are_distinct_or_have_no_ids():
    assert "duplicate_rows" not in [c.name for c in _review([{"uuid": "Q-1"}, {"uuid": "Q-2"}]).checks if c.fired]
    assert "duplicate_rows" not in [c.name for c in _review([{"n": 5}]).checks if c.fired]


def test_an_id_column_is_not_a_sample_id():
    """Review F4: `st.id AS id` is a study id; three rows over two studies are three rows, not two samples."""
    rows = [{"id": 7, "type": "TIS"}, {"id": 7, "type": "MUS"}, {"id": 9, "type": "TIS"}]
    assert sample_ids(rows) is None
    assert "duplicate_rows" not in [c.name for c in _review(rows).checks if c.fired]


def test_uuid_rows_still_count_distinct_samples():
    rows = [{"uuid": "ZZZ-990101ABC-1-PUB", "id": 7}, {"uuid": "ZZZ-990101ABC-1-PUB", "id": 8},
            {"uuid": "YYY-990102DEF-2-PUB", "id": 7}]
    assert sample_ids(rows) == ["ZZZ-990101ABC-1-PUB", "ZZZ-990101ABC-1-PUB", "YYY-990102DEF-2-PUB"]
    review = _review(rows, total=2)
    assert "3 rows for 2 distinct samples" in review.disclosure


# ---- review N2: the holding-study note ----------------------------------------------------------------------------

def test_a_bare_unpublished_test_is_not_a_holding_study():
    from chat_nextseek.graph_review import holding_study_note
    cypher = "MATCH (s:Sample)-[:IN_STUDY]->(st:Study) WHERE NOT st.title ENDS WITH 'Unpublished' RETURN count(s) AS n"
    assert holding_study_note(cypher, {}, [{"n": 4}]) is None


def test_a_list_of_studies_is_not_one_holding_study():
    from chat_nextseek.graph_review import holding_study_note
    rows = [{"title": "Zeta Unpublished"}, {"title": "Quill Unpublished"}]
    assert holding_study_note("MATCH (st:Study) RETURN st.title AS title", {}, rows) is None


def test_one_holding_study_by_its_rows_or_a_parameter_gets_the_note():
    from chat_nextseek.graph_review import HOLDING_STUDY_NOTE, holding_study_note
    rows = [{"title": "Zeta Unpublished", "n": 3}, {"title": "Zeta Unpublished", "n": 1}]
    assert holding_study_note("MATCH (st:Study) RETURN st.title AS title, 1 AS n", {}, rows) == HOLDING_STUDY_NOTE
    cypher = "MATCH (s)-[:IN_STUDY]->(st:Study) WHERE st.title = $t RETURN count(s) AS n"
    assert holding_study_note(cypher, {"t": "Quill Unpublished"}, [{"n": 2}]) == HOLDING_STUDY_NOTE


class _FakeSession:
    def execute_read(self, fn, cypher, params, is_admin):
        return [{"uuid": "ZZZ-1", "st": "S1"}, {"uuid": "ZZZ-1", "st": "S2"}, {"uuid": "ZZZ-2", "st": "S1"}], {}


def test_the_tool_reports_distinct_samples_as_the_total(monkeypatch):
    import sys, types
    from chat_nextseek.graph_scope import GraphScope, SCOPE_ATTR
    session = _FakeSession()

    class Driver:
        def session(self, database=None):
            class Ctx:
                def __enter__(s): return session
                def __exit__(s, *a): return False
            return Ctx()
        def close(self): pass

    fake = types.SimpleNamespace(GraphDatabase=types.SimpleNamespace(driver=lambda *a, **k: Driver()),
                                 unit_of_work=lambda timeout=None: (lambda f: f))
    monkeypatch.setitem(sys.modules, "neo4j", fake)
    config = types.SimpleNamespace(NEO4J_URI="x", NEO4J_USER="u", NEO4J_PASSWORD="p")
    setattr(config, SCOPE_ATTR, GraphScope.admin("test"))
    out = neo4j_tool.tool_neo4j_query(config, "MATCH (s:Sample)-[:IN_STUDY]->(st:Study) "
                                              "RETURN s.uuid AS uuid, st.title AS st")
    assert out["ok"] and out["count"] == 3 and out["total"] == 2


# ---- U2.7 --------------------------------------------------------------------------------------------------------

def test_the_rewrite_limits_are_productions_measured_values():
    from nextseek_api.graph_sync import targeted
    assert (targeted.PARTNER_REWRITE_MAX, targeted.ASSAY_REWRITE_MAX) == (1_000, 50_000)


# ---- U1: the prompt edits ----------------------------------------------------------------------------------------

def test_the_schema_no_longer_says_samples_always_carry_the_paper_ids():
    assert "its samples may carry their papers' DOI and PMID" in STRUCTURE
    assert "its samples carry their papers' DOI" not in STRUCTURE


@pytest.mark.parametrize("phrase", [
    "DOI and PMID are UPPERCASE properties of a paper's Study node",
    "which of the two holds them differs by instance",
    "matches a sample when its own list holds the id OR it is IN_STUDY to a Study",
    "toString(st.PMID) = $pmid",
    "never answer none from one place",
    "`collect(DISTINCT st.title) AS studies`, one row per sample",
    "A column from a to-many relationship beside the sample",
    "A whole identifier (a DOI, PMID, UID or accession number) goes to neither",
    "not a whole identifier, STEP 2",
    "but never a whole identifier (STEP 2);",
    "**Are two named samples related**",
    "an Assay node also holds each assay kind",
    "**A holding study**",
    "**The tool's total counts distinct samples only when every row returns the sample's `uuid`; otherwise it counts "
    "rows.**",
])
def test_every_graph_agent_edit_is_in_the_prompt(phrase):
    assert phrase in AGENT


def test_the_old_wordings_are_gone():
    for old in ("The tool's total counts rows, not samples.","are UPPERCASE properties of the samples, and also of",
                "and every sample carrying it with the studies those samples are IN_STUDY to",
                "- **Assays and protocols** live on the edge. Match"):
        assert old not in AGENT
