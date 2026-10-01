"""The graph-only adapter (tool spec 4.3, T27)."""
from nextseek_api.studies.sources import graph_only, matching
from nextseek_api.studies.tests.conftest import FakeDriver, FakeReader


def _paper(pid, *, title="Paper One", inv_ids=(7,), inv_titles=("Alpha Investigation",), doi="10.0000/one",
           pmid=1111):
    return {"id": pid, "title": title, "description": f"About {title}", "doi": doi, "pmid": pmid,
            "investigation_ids": list(inv_ids), "investigation_titles": list(inv_titles)}


def _driver(papers, members):
    return FakeDriver({graph_only.GRAPH_ONLY_PAPERS: lambda p: papers,
                       graph_only.PAPER_SAMPLES: lambda p: [{"id": i} for i in members.get(p["paper_id"], [])]})


def test_a_paper_becomes_a_target_with_its_facts(alpha):
    driver = _driver([_paper(90)], {90: [2, 3]})
    aset = graph_only.graph_only_associations(driver, "neo4j", "all", FakeReader(alpha), now="t")
    [t] = aset.targets
    assert (t.key, t.investigation_id, t.title, t.doi, t.pmid, t.sample_ids) == (
        "graph_only:90", 7, "Paper One", "10.0000/one", "1111", [2, 3])
    assert aset.source == "graph_only" and all(c.read for c in driver.calls)


def test_the_investigation_must_agree_with_seek_by_id_and_title(alpha):
    driver = _driver([_paper(90, inv_titles=("Alpha Investigation Legacy",)), _paper(91, inv_ids=(1234,)),
                      _paper(92, inv_ids=(7, 8), inv_titles=("Alpha Investigation", "Beta Investigation")),
                      _paper(93, inv_titles=("  alpha investigation ",))],
                     {90: [2], 91: [3], 92: [4], 93: [1]})
    aset = graph_only.graph_only_associations(driver, "neo4j", "all", FakeReader(alpha))
    assert [t.key for t in aset.targets] == ["graph_only:93"]
    assert {u.target_key for u in aset.unmatched if u.reason == matching.INVESTIGATION_UNKNOWN} == {
        "graph_only:90", "graph_only:91", "graph_only:92"}


def test_named_papers_and_one_investigation(alpha):
    driver = _driver([_paper(90), _paper(91, inv_ids=(8,), inv_titles=("Beta Investigation",))], {90: [2], 91: [6]})
    assert [t.key for t in graph_only.graph_only_associations(driver, "neo4j", [91], FakeReader(alpha)).targets] == [
        "graph_only:91"]
    assert [t.key for t in graph_only.graph_only_associations(driver, "neo4j", "all", FakeReader(alpha),
                                                              investigation=7).targets] == ["graph_only:90"]


def test_sample_ids_are_checked_against_seeks_samples(alpha):
    aset = graph_only.graph_only_associations(_driver([_paper(90)], {90: [2, 888]}), "neo4j", "all", FakeReader(alpha))
    assert aset.targets[0].sample_ids == [2]
    assert [(u.reason, u.submitted) for u in aset.unmatched] == [(matching.SAMPLE_ID_NOT_FOUND, "888")]


def test_a_rerun_finds_the_papers_seek_study_by_title(alpha):
    from nextseek_api.studies.tests.conftest import StudyRow

    alpha.studies.append(StudyRow(40, 7, "Paper One", "About Paper One"))
    aset = graph_only.graph_only_associations(_driver([_paper(90)], {90: [2]}), "neo4j", "all", FakeReader(alpha))
    assert aset.targets[0].seek_study_id == 40


def test_the_read_skips_seek_keyed_and_doi_less_nodes_in_its_statement():
    text = graph_only.GRAPH_ONLY_PAPERS
    assert "st.seek_study_id IS NULL" in text and "st.id IS NOT NULL" in text
    assert "toString(st.DOI)" in text and "toString(st.PMID)" in text
