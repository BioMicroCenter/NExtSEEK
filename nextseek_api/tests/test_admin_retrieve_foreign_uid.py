"""
``POST /nextseek_api/admin/samples/retrieve/``: a UID outside the caller's projects answers exactly as an unknown one.

The export walks DERIVED_FROM from the requested samples (``getChildrenUIDs`` in ``seek/sample/trees.py``) and keeps
what ``projects_samples`` places in the caller's projects. It used to walk from every requested UID and filter only
afterwards, so a foreign UID answered 200 with the caller's own samples related to it (its parents and children in
their projects), and 404 only when it had none: that confirmed the foreign sample exists and how it relates to the
caller's samples. For anyone but a superuser the walk now starts only from requested samples in the caller's projects,
so a foreign UID is dropped before the graph is read, exactly as an unknown one is. A superuser is unscoped.

Hermetic: Neo4j is a fake adjacency, MySQL a fake ``projects_samples``, SEEK's project list a stub.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory

from seek.sample.table import DBtable_sample

# projects_samples: uuid -> (id, project ids). The caller is a member of project 2.
SAMPLES = {
    "MUS-VIS-2": (12, {"2"}),
    "TIS-VIS-1": (11, {"2"}),
    "SLD-VIS-3": (13, {"2"}),
    "TIS-FOR-1": (21, {"4"}),
}
# DERIVED_FROM, child -> parent. The foreign TIS-FOR-1 sits between two of the caller's samples, neither related to
# the caller's own TIS-VIS-1 except through their shared parent.
PARENTS = {"TIS-VIS-1": ["MUS-VIS-2"], "TIS-FOR-1": ["MUS-VIS-2"], "SLD-VIS-3": ["TIS-FOR-1"]}
MEMBER_PROJECTS = ["2"]


def _walk(root):
    """Every ancestor and every descendant of root, root included, as getChildrenUIDs' Cypher returns them."""
    if root not in SAMPLES:
        return []
    out, stack = {root}, [root]
    while stack:
        for parent in PARENTS.get(stack.pop(), []):
            if parent not in out:
                out.add(parent)
                stack.append(parent)
    down, stack = {root}, [root]
    while stack:
        node = stack.pop()
        for child, parents in PARENTS.items():
            if node in parents and child not in down:
                down.add(child)
                stack.append(child)
    return sorted(out | down)


class World:
    def __init__(self):
        self.walked = []   # the sample_uids each Cypher call started from

    def execute_query(self, cypher, sample_uids=None, **_):
        self.walked.append(list(sample_uids))
        uuids = sorted({u for root in sample_uids for u in _walk(root)})
        return [{"uuids": uuids}], None, None

    def run_query(self, _self, query, withColumns=False, params=None):
        """``DBtable_sample._runQuery`` over the fake tables: the visibility check or the final metadata SELECT."""
        params = [str(p) for p in (params or [])]
        wanted = [p for p in params if p in SAMPLES]
        projects = {p for p in params if p not in SAMPLES}
        if "json_metadata" not in query:            # the visibility check: SELECT DISTINCT s.uuid
            return [(u,) for u in wanted if SAMPLES[u][1] & projects]
        if "projects_samples" in query:             # the caller's final SELECT, scoped
            wanted = [u for u in wanted if SAMPLES[u][1] & projects]
        rows = [(SAMPLES[u][0], 1, u, "{}") for u in wanted]
        return (rows, ["id", "sample_type_id", "uuid", "json_metadata"]) if withColumns else rows


@pytest.fixture
def world():
    w = World()
    driver = MagicMock()
    driver.__enter__ = MagicMock(return_value=driver)
    driver.__exit__ = MagicMock(return_value=False)
    driver.execute_query.side_effect = w.execute_query
    with patch("seek.sample.trees.GraphDatabase") as graph, \
            patch.object(DBtable_sample, "_runQuery", autospec=True, side_effect=w.run_query):
        graph.driver.return_value = driver
        yield w


def _uuids(df):
    return sorted(df["uuid"]) if not df.empty else []


def _dbs():
    """A DBtable_sample built without __init__, which opens a Django cursor."""
    return DBtable_sample.__new__(DBtable_sample)


# --------------------------------------------------------------------------- getChildrenUIDs


def test_a_foreign_uid_is_never_walked_and_answers_as_an_unknown_one(world):
    foreign = _dbs().getChildrenUIDs(["TIS-FOR-1"], MEMBER_PROJECTS, False)
    unknown = _dbs().getChildrenUIDs(["TIS-NOPE"], MEMBER_PROJECTS, False)

    assert _uuids(foreign) == _uuids(unknown) == []
    assert list(foreign.columns) == list(unknown.columns)
    assert not any("TIS-FOR-1" in roots for roots in world.walked)


def test_a_mixed_request_walks_only_from_the_callers_samples(world):
    df = _dbs().getChildrenUIDs(["TIS-VIS-1", "TIS-FOR-1"], MEMBER_PROJECTS, False)

    assert _uuids(df) == ["MUS-VIS-2", "TIS-VIS-1"]
    assert world.walked == [["TIS-VIS-1"]]


def test_a_member_of_no_project_reads_no_graph(world):
    df = _dbs().getChildrenUIDs(["TIS-VIS-1"], [], False)

    assert df.empty and list(df.columns) == ["id", "sample_type_id", "uuid", "json_metadata"]
    assert world.walked == []


def test_a_superuser_walks_from_every_requested_uid(world):
    df = _dbs().getChildrenUIDs(["TIS-FOR-1"], [], True)

    assert _uuids(df) == ["MUS-VIS-2", "SLD-VIS-3", "TIS-FOR-1"]
    assert world.walked == [["TIS-FOR-1"]]


# --------------------------------------------------------------------------- the endpoint


def _retrieve(identifiers, superuser=False):
    from nextseek_api.views import AdminSampleViewSet

    req = APIRequestFactory().post("/")
    req.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=superuser)
    req.data = {"identifiers": list(identifiers)}
    vs = AdminSampleViewSet()
    vs.format_kwarg, vs.kwargs, vs.request = None, {}, req
    seekdb = MagicMock()
    seekdb.getCurrentUser.return_value = {
        "data": {"relationships": {"projects": {"data": [{"id": p} for p in MEMBER_PROJECTS]}}}}
    by_id = {str(sid): uuid for uuid, (sid, _) in SAMPLES.items()}

    def _connect(**_):
        cursor = MagicMock()
        cursor.execute.side_effect = lambda sql, *a: setattr(
            cursor, "_rows", [(int(i), by_id[i]) for i in sql.split("IN (")[1].rstrip(")\n ").split(", ")
                              if i in by_id])
        cursor.fetchall.side_effect = lambda: cursor._rows
        return MagicMock(cursor=MagicMock(return_value=cursor))

    with patch("nextseek_api.views.resolve_seek_auth", return_value=(("member", "pw"), {})), \
            patch("nextseek_api.views.SeekDB", return_value=seekdb), \
            patch("nextseek_api.views.DBtable_sample", side_effect=_dbs), \
            patch("nextseek_api.views.MySQLdb", connect=MagicMock(side_effect=_connect)):
        resp = vs.admin_retrieve_samples(req)
    return resp.status_code, resp.data


@pytest.mark.parametrize("foreign, unknown", [("TIS-FOR-1", "TIS-NOPE"), ("21", "99")], ids=["uid", "seek-id"])
def test_the_endpoint_answers_a_foreign_identifier_as_an_unknown_one(world, foreign, unknown):
    assert _retrieve([foreign]) == _retrieve([unknown])
    assert _retrieve([foreign])[0] == 404


def test_the_endpoint_answers_a_mixed_request_as_if_the_foreign_uid_were_unknown(world):
    with_foreign = _retrieve(["TIS-VIS-1", "TIS-FOR-1"])
    with_unknown = _retrieve(["TIS-VIS-1", "TIS-NOPE"])

    assert with_foreign == with_unknown
    assert with_foreign[0] == 200
    assert with_foreign[1]["total_samples"] == 2 and with_foreign[1]["failed_uids"] == 1


def test_the_endpoint_still_exports_a_superusers_request_whole(world):
    status, data = _retrieve(["TIS-FOR-1"], superuser=True)

    assert status == 200
    assert data["total_samples"] == 3
