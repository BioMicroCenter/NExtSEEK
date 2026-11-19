"""A map may cover a pipeline pinned to a commit (no release yet: nf-core/scdownstream).

`applies_to_commits` lists the 40-hex commits the map was read at, beside or instead of
`applies_to_versions`. `PipelineMap.covers_revision` is the one check both the link test and
anything else use: a release number is looked up in the version range, a commit SHA in the
commit list. A run reports its commit as nf-core's `-g<short sha>` version suffix, which
`effective()` matches against the list."""
import pytest
from pydantic import ValidationError

from NessieAI.ns.reingest import maps

SHA = "d9264b35ed88b648baf157d76bd9b261344fb585"
OTHER = "0123456789abcdef0123456789abcdef01234567"


def _map(**over):
    doc = {"map_version": 2, "pipeline": "nf-core/toy", "outputs": []}
    doc.update(over)
    return maps.PipelineMap.model_validate(doc)


def test_a_release_is_covered_by_the_version_range_only():
    m = _map(applies_to_versions=">=1.0,<1.1")
    assert m.covers_revision("1.0.4") is True
    assert m.covers_revision("1.1.0") is False
    assert m.covers_revision(SHA) is False


def test_a_commit_is_covered_by_the_commit_list_only():
    m = _map(applies_to_commits=[SHA])
    assert m.covers_revision(SHA) is True
    assert m.covers_revision(OTHER) is False
    assert m.covers_revision("1.0.0") is False


def test_a_map_may_name_both():
    m = _map(applies_to_versions=">=1.0,<2", applies_to_commits=[SHA])
    assert m.covers_revision("1.2.0") and m.covers_revision(SHA)


@pytest.mark.parametrize("bad", ["d9264b3", SHA.upper(), SHA + "0", "main", "v1.0.0"])
def test_only_full_lowercase_shas_are_accepted(bad):
    with pytest.raises(ValidationError):
        _map(applies_to_commits=[bad])


def test_a_run_from_a_listed_commit_is_checked():
    m = _map(applies_to_commits=[SHA])
    _, warnings = maps.effective(m, "v0.0.1dev-gd9264b3")
    assert warnings == []


def test_a_run_from_another_commit_still_maps_with_a_warning():
    m = _map(applies_to_commits=[SHA])
    _, warnings = maps.effective(m, "v0.0.1dev-g0123456")
    assert len(warnings) == 1 and "outside the checked" in warnings[0]


def test_a_release_map_is_unchanged_by_the_commit_check():
    m = _map(applies_to_versions=">=1.0,<1.1")
    assert maps.effective(m, "v1.0.2-gd9264b3")[1] == []
    assert len(maps.effective(m, "1.2.0")[1]) == 1
