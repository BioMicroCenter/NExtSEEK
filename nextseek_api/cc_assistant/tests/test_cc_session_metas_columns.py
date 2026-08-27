"""#82: ``_session_metas`` must select only the three columns it reads.

``_session_metas`` sorts EVERY ChatSession the user owns by ``-updated_at``,
unbounded. ``results_history`` was already excluded (#40 / errno 1038, "Out of
sort memory"), but ``last_debug`` -- the other multi-MB JSONField on the same
row -- was still selected into that filesort, along with ``title`` and
``created_at`` which nothing here reads.

The loop body reads exactly three fields off each row: ``session_id`` (the
primary key), ``extra_state`` and ``updated_at``. ``user.username`` comes from
the passed-in ``user`` argument, not from the row.

Two boundaries these tests pin deliberately:

* ``extra_state`` must stay in the SELECT. It is read on every row and
  everything below it depends on it, so deferring it would trade one filesort
  column for one extra query PER ROW.
* No ``LIMIT``. Rows come back ``-updated_at`` DESC and one of the two
  consumers is ``cc_sweep.select_sweep_targets``, which wants the sessions that
  are IDLE -- i.e. the OLDEST ``updated_at``. A ``LIMIT N`` would hand it the N
  LEAST idle sessions and silently stop sweeping everything else.

DB-backed on purpose: the assertions are about the SQL the real call path
compiles, not about a hand-built queryset that nothing executes.
"""
from __future__ import annotations

import dataclasses

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

import nextseek_api.services.cc_assistant as cc_svc
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant import cc_config

pytestmark = pytest.mark.django_db

PROJECT = "1-x"


def _cfg(tmp_path):
    """CC config whose user tree points at an empty tmp dir.

    ``_session_metas`` rglobs ``<cc_state_mnt>/projects`` for transcripts; with
    nothing there the filesystem half of the helper is a no-op and the test is
    only measuring the query.
    """
    paths = dataclasses.replace(
        cc_config.CCPaths.from_env(), user_root_mount=str(tmp_path)
    )
    return paths, cc_config.CCMemoryConfig.from_env()


def _seed(user, n):
    """n sessions carrying real payload in both JSON blob columns."""
    made = []
    for i in range(n):
        made.append(
            ChatSession.objects.create(
                user=user,
                # The two multi-MB-in-production columns.
                results_history=[{"user_query": f"q{i}", "pad": "r" * 200}],
                last_debug={"trace": "d" * 200, "i": i},
                # The one column the loop actually reads.
                extra_state={
                    "cc_project_dirname": PROJECT,
                    "summary": {"gist": f"gist-{i}"},
                    "summary_fingerprint": f"fp-{i}",
                },
                title=f"title-{i}",
            )
        )
    return made


def _ordering_sql(captured):
    return [q["sql"] for q in captured if "ORDER BY" in q["sql"].upper()]


def _select_list(sql):
    """The projected columns only — everything left of the FROM clause."""
    head, sep, _rest = sql.partition(" FROM ")
    assert sep, f"unparseable SELECT: {sql}"
    return head


# ---------------------------------------------------------------------------
# 1. the compiled SQL
# ---------------------------------------------------------------------------


def test_session_metas_sorts_only_over_the_columns_it_reads(tmp_path):
    user = get_user_model().objects.create_user("metas-sql", password="x")
    _seed(user, 3)
    paths, mem_cfg = _cfg(tmp_path)

    with CaptureQueriesContext(connection) as ctx:
        cc_svc._session_metas(user, None, paths, mem_cfg, project_dirname=PROJECT)

    ordering = _ordering_sql(ctx.captured_queries)
    assert len(ordering) == 1, f"expected exactly one ORDER BY query, got {ordering}"
    sql = ordering[0]

    projected = _select_list(sql)
    for col in ("session_id", "extra_state", "updated_at"):
        assert col in projected, f"{col} must be selected, not lazily re-fetched: {sql}"

    for col in ("last_debug", "results_history"):
        assert col not in sql, f"multi-MB {col} entered the filesort: {sql}"


def test_session_metas_does_not_limit_the_sweep(tmp_path):
    """No LIMIT: ``cc_sweep`` needs the OLDEST rows, and this is DESC.

    ``select_sweep_targets`` keeps sessions whose ``updated_at`` is at least
    ``sweep_idle_seconds`` old. Truncating a ``-updated_at`` DESC scan keeps the
    N most recently touched sessions — precisely the ones the sweep is meant to
    skip — so every genuinely idle session would stop being summarized, with no
    error anywhere.
    """
    user = get_user_model().objects.create_user("metas-nolimit", password="x")
    _seed(user, 4)
    paths, mem_cfg = _cfg(tmp_path)

    with CaptureQueriesContext(connection) as ctx:
        metas = cc_svc._session_metas(user, None, paths, mem_cfg, project_dirname=PROJECT)

    sql = _ordering_sql(ctx.captured_queries)[0]
    assert "LIMIT" not in sql.upper(), f"bounded the sweep's own input: {sql}"
    assert len(metas) == 4


# ---------------------------------------------------------------------------
# 2. no extra query per row
# ---------------------------------------------------------------------------


def _query_count(user, tmp_path):
    paths, mem_cfg = _cfg(tmp_path)
    with CaptureQueriesContext(connection) as ctx:
        metas = cc_svc._session_metas(user, None, paths, mem_cfg, project_dirname=PROJECT)
    return len(ctx.captured_queries), metas


def test_session_metas_query_count_does_not_grow_with_session_count(tmp_path):
    """Guards the wrong fix: deferring ``extra_state`` as well.

    The issue body suggests deferring ``last_debug`` *and* ``extra_state``.
    ``extra_state`` is read on every single row, so deferring it turns one
    query into 1 + N.
    """
    User = get_user_model()
    few = User.objects.create_user("metas-few", password="x")
    many = User.objects.create_user("metas-many", password="x")
    _seed(few, 1)
    _seed(many, 6)

    n_few, metas_few = _query_count(few, tmp_path / "few")
    n_many, metas_many = _query_count(many, tmp_path / "many")

    assert len(metas_few) == 1 and len(metas_many) == 6, "fixture did not seed"
    assert n_few == n_many, (
        f"query count grew with row count ({n_few} -> {n_many}): a field the "
        f"loop reads is deferred and re-fetched per row"
    )
    assert n_many == 1, f"expected a single query, got {n_many}"


def test_session_metas_reads_extra_state_without_a_second_query(tmp_path):
    """The values that come off ``extra_state`` are really there.

    Without this the count test above could pass on rows whose ``extra_state``
    is empty, which is not the shape the deferral would be paid on.
    """
    user = get_user_model().objects.create_user("metas-extra", password="x")
    _seed(user, 3)
    paths, mem_cfg = _cfg(tmp_path)

    metas = cc_svc._session_metas(user, None, paths, mem_cfg, project_dirname=PROJECT)

    assert {m.fingerprint for m in metas} == {"fp-0", "fp-1", "fp-2"}
    assert {m.summary["gist"] for m in metas} == {"gist-0", "gist-1", "gist-2"}


# ---------------------------------------------------------------------------
# 3. behaviour is unchanged
# ---------------------------------------------------------------------------


def test_session_metas_still_returns_every_session_newest_first(tmp_path):
    user = get_user_model().objects.create_user("metas-behaviour", password="x")
    sessions = _seed(user, 4)
    paths, mem_cfg = _cfg(tmp_path)

    metas = cc_svc._session_metas(user, None, paths, mem_cfg, project_dirname=PROJECT)

    expected = sorted(sessions, key=lambda s: s.updated_at, reverse=True)
    assert [m.session_id for m in metas] == [str(s.session_id) for s in expected]
    assert [m.updated_at for m in metas] == [s.updated_at.timestamp() for s in expected]
    assert all(m.transcript_path is None and m.changed is False for m in metas)


def test_session_metas_is_still_scoped_to_the_users_own_sessions(tmp_path):
    """The user filter is load-bearing (nessie_tests documents it); the column
    narrowing must not disturb it."""
    User = get_user_model()
    mine = User.objects.create_user("metas-mine", password="x")
    theirs = User.objects.create_user("metas-theirs", password="x")
    ours = _seed(mine, 2)
    _seed(theirs, 3)
    paths, mem_cfg = _cfg(tmp_path)

    metas = cc_svc._session_metas(mine, None, paths, mem_cfg, project_dirname=PROJECT)

    assert {m.session_id for m in metas} == {str(s.session_id) for s in ours}
