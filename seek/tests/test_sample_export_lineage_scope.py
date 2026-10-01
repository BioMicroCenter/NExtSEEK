"""A legacy sample export's lineage keeps only the caller's samples.

With ``includeSampleTree=1`` (and always for the ImmPort export) the legacy exports walk each requested sample's
parents and children and write every sample on each path into the workbook. Starting from a sample in the caller's
projects, that walk reaches relatives in other projects, whose metadata then lands in the caller's file. The three
path builders (``_createSampleTreeFromDB``, ``_createSampleTreeFromDB_noTree`` and ``_createSampleTree``) therefore
drop from every path the samples outside the projects set by ``restrictToProjects``, before headers or rows are built,
with ``getVisibleUIDs`` (``projects_samples``, the check the admin retrieve walk uses). Unset, as for a superuser, the
paths are unchanged.

Hermetic: the walk is a fixed tree, ``projects_samples`` a fake, and the row builders are captured.
"""

from unittest.mock import MagicMock, patch

import pytest

from seek.sample.table import DBtable_sample

# uuid -> projects. The caller is a member of project 2; MUS-FOR-1 is a parent in project 4.
PROJECTS = {"MUS-FOR-1": {"4"}, "TIS-VIS-1": {"2"}, "SLD-VIS-2": {"2"}, "SLD-FOR-3": {"4"}}
# One lineage, MUS-FOR-1 -> TIS-VIS-1 -> {SLD-VIS-2, SLD-FOR-3}, as _createMultiParentTree hands it back.
TREE = [{"id": "MUS-FOR-1", "name": "MUS-FOR-1", "children": [
    {"id": "TIS-VIS-1", "name": "TIS-VIS-1", "children": [
        {"id": "SLD-VIS-2", "name": "SLD-VIS-2"},
        {"id": "SLD-FOR-3", "name": "SLD-FOR-3"},
    ]},
]}]
ALL_PATHS = [["MUS-FOR-1", "TIS-VIS-1", "SLD-VIS-2"], ["MUS-FOR-1", "TIS-VIS-1", "SLD-FOR-3"]]
MEMBER_PATHS = [["TIS-VIS-1", "SLD-VIS-2"], ["TIS-VIS-1"]]


@pytest.fixture
def world():
    captured, queries = {}, []

    def run_query(_self, query, withColumns=False, params=None):   # getVisibleUIDs' SELECT over projects_samples
        queries.append(query)
        params = [str(p) for p in params or []]
        return [(u,) for u in params if u in PROJECTS and PROJECTS[u] & set(params)]

    def capture(name, result):
        def _record(_self, parentList, *rest):
            captured[name] = [list(path) for path in parentList]
            return result
        return _record

    sample_tree = MagicMock()
    sample_tree.objects.filter.return_value.count.return_value = 0     # no cached tree: walk it
    with patch.object(DBtable_sample, "_runQuery", autospec=True, side_effect=run_query), \
            patch.object(DBtable_sample, "_createMultiParentTree", autospec=True, return_value=(TREE, [])), \
            patch.object(DBtable_sample, "_getSampleTypeAttributes", autospec=True,
                         side_effect=capture("attributes", ([], {}, [], {}))), \
            patch.object(DBtable_sample, "_convertSampleTreeToList", autospec=True,
                         side_effect=capture("rows", ([], []))), \
            patch.object(DBtable_sample, "_getTreeSampleTypes", autospec=True, side_effect=capture("types", {})), \
            patch("seek.models.Sample_tree", sample_tree):
        yield captured, queries


def _dbs(project_ids="unset"):
    """A DBtable_sample built without __init__ (which opens a Django cursor), scoped as asked."""
    dbs = DBtable_sample.__new__(DBtable_sample)
    if project_ids != "unset":
        dbs.restrictToProjects(project_ids)
    return dbs


@pytest.mark.parametrize("build", ["_createSampleTreeFromDB", "_createSampleTree"])
def test_a_members_tree_export_never_carries_a_relative_from_another_project(world, build):
    captured, _ = world

    getattr(_dbs((2, 13)), build)([11])

    assert captured["attributes"] == MEMBER_PATHS
    assert captured["rows"] == MEMBER_PATHS


def test_a_members_filtered_tree_export_never_carries_a_relative_from_another_project(world):
    captured, _ = world

    _dbs((2, 13))._createSampleTreeFromDB_noTree([11])

    assert captured["types"] == MEMBER_PATHS


def test_a_member_of_no_project_gets_no_path_at_all(world):
    captured, _ = world

    _dbs(())._createSampleTreeFromDB([11])

    assert captured["rows"] == []


@pytest.mark.parametrize("scope", [None, "unset"], ids=["superuser", "never-restricted"])
@pytest.mark.parametrize("build", ["_createSampleTreeFromDB", "_createSampleTree"])
def test_an_unscoped_tree_export_is_unchanged(world, build, scope):
    captured, queries = world

    getattr(_dbs(scope), build)([11])

    assert captured["rows"] == ALL_PATHS
    assert queries == []
