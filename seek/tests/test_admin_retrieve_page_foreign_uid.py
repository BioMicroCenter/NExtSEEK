"""``POST /seek/admin/retrieve/`` (``seek.views.admin.get_children_uids``): a foreign UID reads as an unknown one.

The twin of ``getChildrenUIDs``, once behind the download API (``/nextseek_api/samples/retrieve/``)
(``nextseek_api/tests/test_admin_retrieve_foreign_uid.py``), and it had the same defect: the graph walk started from
every requested UID and only the final SELECT was scoped, so a UID outside the caller's projects produced a file with
the caller's own samples related to it. For anyone but a superuser the walk now starts only from requested samples in
the caller's projects (``DBtable_sample.getVisibleUIDs``, one definition for both paths).

Hermetic: Neo4j is a fake adjacency, the visibility check and the final SELECT read a fake ``projects_samples``.
"""

from unittest.mock import MagicMock, patch

import pytest

import seek.views.admin  # noqa: F401  -- so @patch can resolve the target
from seek.sample.table import DBtable_sample

_MOD = "seek.views.admin"
# uuid -> (id, project ids). The caller is a member of project 2.
SAMPLES = {"MUS-VIS-2": (12, {"2"}), "TIS-VIS-1": (11, {"2"}), "SLD-VIS-3": (13, {"2"}), "TIS-FOR-1": (21, {"4"})}
# DERIVED_FROM, child -> parent: the foreign TIS-FOR-1 has a parent and a child in the caller's project.
RELATIVES = {"TIS-VIS-1": ["TIS-VIS-1", "MUS-VIS-2"], "TIS-FOR-1": ["TIS-FOR-1", "MUS-VIS-2", "SLD-VIS-3"]}


@pytest.fixture
def world():
    walked = []

    def execute_query(cypher, sample_uids=None, **_):
        walked.append(list(sample_uids))
        return [{"uuids": sorted({u for root in sample_uids for u in RELATIVES.get(root, [])})}], None, None

    def run_query(_self, query, withColumns=False, params=None):   # the visibility check
        params = [str(p) for p in params or []]
        return [(u,) for u in params if u in SAMPLES and SAMPLES[u][1] & set(params)]

    cursor = MagicMock()

    def execute(sql, params):                                        # the final SELECT, scoped
        cursor.rows = [(SAMPLES[u][0], 1, u, "{}") for u in params
                       if u in SAMPLES and SAMPLES[u][1] & set(params)]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: cursor.rows
    cursor.description = [("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)]
    driver = MagicMock()
    driver.__enter__ = MagicMock(return_value=driver)
    driver.__exit__ = MagicMock(return_value=False)
    driver.execute_query.side_effect = execute_query
    with patch(f"{_MOD}.GraphDatabase") as graph, \
            patch(f"{_MOD}.MySQLdb") as mysql, \
            patch(f"{_MOD}.DBtable_sample", side_effect=lambda: DBtable_sample.__new__(DBtable_sample), create=True), \
            patch.object(DBtable_sample, "_runQuery", autospec=True, side_effect=run_query):
        graph.driver.return_value = driver
        mysql.connect.return_value.cursor.return_value = cursor
        yield walked


def _uuids(df):
    return sorted(df["uuid"]) if not df.empty else []


def test_a_foreign_uid_is_never_walked_and_answers_as_an_unknown_one(world):
    foreign = seek.views.admin.get_children_uids(["TIS-FOR-1"], iter(["2"]), False)
    unknown = seek.views.admin.get_children_uids(["TIS-NOPE"], iter(["2"]), False)

    assert _uuids(foreign) == _uuids(unknown) == []
    assert list(foreign.columns) == list(unknown.columns)
    assert not any("TIS-FOR-1" in roots for roots in world)


def test_a_mixed_request_walks_only_from_the_callers_samples(world):
    df = seek.views.admin.get_children_uids(["TIS-VIS-1", "TIS-FOR-1"], iter(["2"]), False)

    assert _uuids(df) == ["MUS-VIS-2", "TIS-VIS-1"]
    assert world == [["TIS-VIS-1"]]
