import json

import pytest
from pydantic import ValidationError

from NessieAI.ns.paper.rulebook import RULEBOOK_PATH, Rule, applicable, load_rulebook, rulebook_version


def test_the_seed_rulebook_loads_and_its_ids_are_unique():
    book = load_rulebook()
    ids = [r.id for r in book.rules]
    assert len(ids) == len(set(ids)) and "R-UID-DATE" in ids and "R-TIER-MUS-TIS-CEL" in ids


def test_every_constraint_names_a_registered_check():
    check = pytest.importorskip("NessieAI.ns.paper.curation_check")
    for rule in load_rulebook().rules:
        if rule.kind == "constraint":
            assert rule.check in check.CONSTRAINT_CHECKS, rule.id
        if rule.check is not None:
            assert rule.check in check.CONSTRAINT_CHECKS, rule.id


def test_a_constraint_without_a_check_is_refused():
    with pytest.raises(ValidationError, match="check"):
        Rule(id="R-X", scope="global", kind="constraint", statement="s", origin="o")


@pytest.mark.parametrize("scope", ["global", "project:10", "technology:RNA-seq", "sample_type:D.SEQ"])
def test_scopes(scope):
    assert Rule(id="R-X", scope=scope, kind="default", statement="s", origin="o").scope == scope


@pytest.mark.parametrize("scope", ["project:", "everywhere", "sample_type:"])
def test_bad_scopes_are_refused(scope):
    with pytest.raises(ValidationError):
        Rule(id="R-X", scope=scope, kind="default", statement="s", origin="o")


def test_applicable_by_scope():
    def rule(scope):
        return Rule(id="R-X", scope=scope, kind="default", statement="s", origin="o")

    kw = {"project_id": 10, "sample_types": {"MUS", "D.SEQ"}, "assays": {"RNA-seq - Data Linked"}}
    assert applicable(rule("global"), **kw)
    assert applicable(rule("project:10"), **kw) and not applicable(rule("project:4"), **kw)
    assert applicable(rule("sample_type:D.SEQ"), **kw) and not applicable(rule("sample_type:TIS"), **kw)
    assert applicable(rule("technology:rna-seq"), **kw) and not applicable(rule("technology:ATAC"), **kw)
    assert not applicable(rule("project:10"), project_id=None, sample_types=set(), assays=set())


def test_the_version_is_the_file_hash(tmp_path):
    copy = tmp_path / "rulebook.json"
    copy.write_bytes(RULEBOOK_PATH.read_bytes())
    assert rulebook_version(copy) == rulebook_version()
    copy.write_text(json.dumps({**json.loads(copy.read_text()), "rulebook_version": 1}) + "\n")
    assert len(rulebook_version(copy)) == 64
