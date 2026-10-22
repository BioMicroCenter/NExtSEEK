"""samples_editable_by: which samples SEEK would let this person edit (checked against SEEK 1.15.1)."""
import pytest
from sqlalchemy import create_engine, text

from nextseek_api.batch_upload.edit_rights import samples_editable_by

ME = 5


@pytest.fixture
def conn():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, contributor_id INTEGER, policy_id INTEGER)"))
        c.execute(text("CREATE TABLE policies (id INTEGER PRIMARY KEY, access_type INTEGER)"))
        c.execute(text("CREATE TABLE permissions (id INTEGER PRIMARY KEY, policy_id INTEGER, "
                       "contributor_type TEXT, contributor_id INTEGER, access_type INTEGER)"))
    with engine.connect() as c:
        yield c


def _sample(conn, sid, *, contributor=99, general=0, perms=()):
    conn.execute(text("INSERT INTO policies VALUES (:i, :a)"), {"i": sid, "a": general})
    conn.execute(text("INSERT INTO samples VALUES (:i, :c, :i)"), {"i": sid, "c": contributor})
    for kind, cid, access in perms:
        conn.execute(text("INSERT INTO permissions (policy_id, contributor_type, contributor_id, access_type) "
                          "VALUES (:p, :k, :c, :a)"), {"p": sid, "k": kind, "c": cid, "a": access})


def test_contributor_allowed(conn):
    _sample(conn, 1, contributor=ME)
    assert samples_editable_by([1], ME, (7,), conn) == {1}


def test_project_permission_at_edit_allowed(conn):
    _sample(conn, 1, perms=[("Project", 7, 4)])
    assert samples_editable_by([1], ME, (7,), conn) == {1}


def test_project_permission_of_another_project_refused(conn):
    _sample(conn, 1, perms=[("Project", 8, 4)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_project_permission_view_only_refused(conn):
    _sample(conn, 1, perms=[("Project", 7, 1)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_person_permission_overrides_project_permission(conn):
    _sample(conn, 1, perms=[("Project", 7, 4), ("Person", ME, 1)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_person_permission_at_edit_beats_view_only_project(conn):
    _sample(conn, 1, perms=[("Project", 7, 1), ("Person", ME, 3)])
    assert samples_editable_by([1], ME, (7,), conn) == {1}


def test_policy_general_edit_allowed_even_with_low_person_permission(conn):
    # SEEK ORs the policy check with the permission check.
    _sample(conn, 1, general=3, perms=[("Person", ME, 1)])
    assert samples_editable_by([1], ME, (7,), conn) == {1}


def test_policy_general_view_refused(conn):
    _sample(conn, 1, general=2)
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_workgroup_only_refused(conn):
    _sample(conn, 1, perms=[("WorkGroup", 3, 4)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_a_group_permission_could_override_the_project_one_so_refused(conn):
    _sample(conn, 1, perms=[("Project", 7, 4), ("WorkGroup", 3, 1)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_mixed_batch_and_empty_inputs(conn):
    _sample(conn, 1, contributor=ME)
    _sample(conn, 2)
    assert samples_editable_by([1, 2], ME, (7,), conn) == {1}
    assert samples_editable_by([], ME, (7,), conn) == set()
    assert samples_editable_by([2], ME, (), conn) == set()


def test_publishing_access_five_does_not_edit(conn):
    # SEEK: edit is 3..4; 5 is publishing only.
    _sample(conn, 1, general=5)
    _sample(conn, 2, perms=[("Project", 7, 5)])
    _sample(conn, 3, perms=[("Person", ME, 5)])
    assert samples_editable_by([1, 2, 3], ME, (7,), conn) == set()


def test_another_persons_permission_does_not_decide_for_me(conn):
    # Mutant guard: a Person row for someone else must not stand in for mine.
    _sample(conn, 1, perms=[("Project", 7, 4), ("Person", 99, 1)])
    _sample(conn, 2, perms=[("Person", 99, 4)])
    assert samples_editable_by([1, 2], ME, (7,), conn) == {1}


def test_favourite_group_row_is_refused_not_ignored(conn):
    # Mutant guard: FavouriteGroup outranks Project in SEEK, so it is a refusal like WorkGroup.
    _sample(conn, 1, perms=[("Project", 7, 4), ("FavouriteGroup", 3, 1)])
    assert samples_editable_by([1], ME, (7,), conn) == set()


def test_verdicts_tell_a_seek_refusal_from_an_unreadable_group_grant(conn):
    from nextseek_api.batch_upload.edit_rights import edit_verdicts

    _sample(conn, 1, perms=[("Project", 7, 1)])
    _sample(conn, 2, perms=[("WorkGroup", 3, 4)])
    _sample(conn, 3, contributor=ME)
    assert edit_verdicts([1, 2, 3], ME, (7,), conn) == {1: "no", 2: "unknown", 3: "yes"}
