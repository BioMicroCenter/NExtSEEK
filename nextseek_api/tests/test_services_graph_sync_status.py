"""GET /nextseek_api/admin/graph-sync/status/: the superuser view of the graph sync's own state (the spec's 13).

Hermetic: the two dmac tables on the SQLite test settings, no MySQL, no Neo4j and no live stack. The gates and the
envelope go through the real DRF dispatch at the real path, so the 401 the endpoint owes an anonymous caller is the
one DRF actually sends; the time-dependent half calls ``build_status`` with an explicit ``now``, because a request
carries no clock the test can set.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth.models import User
from django.db import DatabaseError, connection
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

from nextseek_api.graph_sync import state, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun
from nextseek_api.services import graph_sync_status

PATH = "/nextseek_api/admin/graph-sync/status/"
T0 = datetime(2026, 9, 15, 2, 0, tzinfo=dt_timezone.utc)

PARTS = ("generated_at", "schema_version", "runs", "freshness", "outbox", "drift")


def at(**delta) -> datetime:
    return T0 + timedelta(**delta)


def drop_table(name: str) -> None:
    """Drop a table inside the test's transaction; the rollback at the end of the test restores it."""
    with connection.cursor() as cur:
        cur.execute(f'DROP TABLE "{name}"')


def superuser() -> User:
    return User.objects.create_user(username="root", password="x", is_staff=True, is_superuser=True)


def seek_user() -> User:
    """A SEEK login: is_staff is set on every one of them, is_superuser on none."""
    return User.objects.create_user(username="labuser", password="x", is_staff=True, is_superuser=False)


def as_superuser() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=superuser())
    return client


def a_full_run(status: str = "ok", *, started=None, schema_version: str | None = None):
    handle = state.start_run("full", trigger="command", now=started)
    handle.finish(status, counts={"schema_version": schema_version or writer.SCHEMA_VERSION, "samples": 12},
                  now=started)
    return handle


# --- the route ------------------------------------------------------------------------------------

def test_the_status_action_is_registered_at_its_own_path():
    assert reverse("nextseek_api:admin-graph-sync-status") == PATH


def test_the_registration_publishes_no_list_route():
    """No list route, so the API root does not advertise the admin surface (the spec's 13)."""
    with pytest.raises(NoReverseMatch):
        reverse("nextseek_api:admin-graph-sync-list")


# --- the gates ------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_an_anonymous_caller_is_refused_with_401():
    response = APIClient().get(PATH)
    assert response.status_code == 401


@pytest.mark.django_db
def test_an_authenticated_non_superuser_is_refused_with_403():
    """is_staff is what a SEEK login sets on everyone, so only is_superuser may pass."""
    client = APIClient()
    client.force_authenticate(user=seek_user())
    assert client.get(PATH).status_code == 403


# --- the body -------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_superuser_gets_every_part():
    a_full_run()
    state.enqueue("samples", "sample:7")

    response = as_superuser().get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert sorted(body) == sorted(PARTS)
    assert body["schema_version"] == writer.SCHEMA_VERSION
    assert body["runs"]["full"]["status"] == "ok"
    assert body["runs"]["full"]["counts"]["schema_version"] == writer.SCHEMA_VERSION
    assert body["freshness"]["full"]["status"] == "ok"
    assert body["outbox"]["pending"] == {"samples": 1}


@pytest.mark.django_db
def test_the_latest_run_of_each_kind_is_reported_whatever_its_status():
    a_full_run("failed")
    a_full_run("ok")
    state.start_run("reconcile", trigger="loop").finish("refused")

    runs = as_superuser().get(PATH).json()["runs"]

    assert sorted(runs) == ["full", "reconcile"]
    assert runs["full"]["status"] == "ok"
    assert runs["reconcile"]["status"] == "refused"


@pytest.mark.django_db
def test_the_outbox_part_counts_the_open_rows_and_names_the_oldest():
    state.enqueue("catalog", "*", now=T0)
    state.enqueue("samples", "sample:7", now=at(minutes=5))
    state.enqueue("retire", "sample:9", now=at(minutes=9))

    outbox = as_superuser().get(PATH).json()["outbox"]

    assert outbox["pending"] == {"catalog": 1, "samples": 1, "retire": 1}
    assert outbox["dead"] == {}
    assert outbox["oldest_pending"]["key"] == "*"
    assert outbox["oldest_pending"]["kind"] == "catalog"
    assert outbox["max_attempts"] == state.MAX_ATTEMPTS


@pytest.mark.django_db
def test_the_latest_drift_result_is_reported():
    state.start_run("drift", trigger="loop").finish("drift", drift={"checks": {"9.lineage.labels": "fail"}})

    body = as_superuser().get(PATH).json()

    assert body["drift"] == {"checks": {"9.lineage.labels": "fail"}}
    assert body["runs"]["drift"]["status"] == "drift"


@pytest.mark.django_db
def test_the_drift_part_is_null_when_no_drift_run_has_recorded_one():
    assert as_superuser().get(PATH).json()["drift"] is None


@pytest.mark.django_db
def test_the_endpoint_writes_nothing():
    """Read-only: reading the status must not touch either table (the spec's 13)."""
    a_full_run()
    state.enqueue("isa", "*")
    before = (GraphSyncOutbox.objects.count(), GraphSyncRun.objects.count(),
              list(GraphSyncOutbox.objects.values_list("enqueued_at", "attempts", "claimed_by", "done_at")))

    assert as_superuser().get(PATH).status_code == 200

    assert (GraphSyncOutbox.objects.count(), GraphSyncRun.objects.count(),
            list(GraphSyncOutbox.objects.values_list(
                "enqueued_at", "attempts", "claimed_by", "done_at"))) == before


# --- freshness, which needs a clock the request cannot carry --------------------------------------

@pytest.mark.django_db
def test_freshness_reports_never_before_any_run():
    body = graph_sync_status.build_status(now=T0)

    assert body["freshness"]["full"]["status"] == "never"
    assert body["freshness"]["reconcile"]["status"] == "never"
    assert body["freshness"]["full"]["last_ok_started_at"] is None
    assert body["freshness"]["outbox"]["status"] == "ok"


@pytest.mark.django_db
def test_freshness_goes_stale_once_a_run_is_older_than_its_threshold():
    a_full_run(started=T0)

    assert graph_sync_status.build_status(now=at(days=1))["freshness"]["full"]["status"] == "ok"
    assert graph_sync_status.build_status(now=at(days=9))["freshness"]["full"]["status"] == "stale"


@pytest.mark.django_db
def test_a_full_sync_satisfies_the_reconcile_and_a_waiting_row_ages_the_outbox():
    a_full_run(started=T0)
    state.enqueue("samples", "sample:7", now=T0)

    fresh = graph_sync_status.build_status(now=at(minutes=30))["freshness"]

    assert fresh["reconcile"]["satisfied_by"] == "full"
    assert fresh["outbox"]["status"] == "ok"
    assert graph_sync_status.build_status(now=at(hours=3))["freshness"]["outbox"]["status"] == "stale"


# --- when the tables cannot be read ---------------------------------------------------------------

@pytest.mark.django_db
def test_build_status_raises_when_a_table_is_missing():
    drop_table(GraphSyncRun._meta.db_table)

    with pytest.raises(DatabaseError):
        graph_sync_status.build_status(now=T0)


@pytest.mark.django_db
def test_the_endpoint_answers_503_in_the_json_api_envelope_when_the_tables_cannot_be_read():
    """Production runs a v1.0 graph without migration 0021, so this is a live shape, not a hypothetical."""
    drop_table(GraphSyncRun._meta.db_table)

    response = as_superuser().get(PATH)

    assert response.status_code == 503
    assert [error["title"] for error in response.json()["errors"]] == [graph_sync_status.UNAVAILABLE_TITLE]


@pytest.mark.django_db
def test_the_503_body_is_fixed_prose_rather_than_the_drivers_message():
    """A driver's message names schemas, hosts and statements, and this body reaches CI logs and terminals."""
    drop_table(GraphSyncOutbox._meta.db_table)

    detail = as_superuser().get(PATH).json()["errors"][0]["detail"]

    assert detail == graph_sync_status.UNAVAILABLE_DETAIL
    assert "0021" in detail, "the detail should say what an operator can do about it"
    assert "no such table" not in detail.lower(), "the driver's own message reached the response"


# --- the published schema -------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_path_and_the_response_model_are_in_the_openapi_schema():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)

    assert PATH in schema["paths"]
    operation = schema["paths"][PATH]["get"]
    assert operation["operationId"] == "Admin: Graph Sync Status"
    assert "admin" in operation["tags"]
    assert "GraphSyncStatusResponse" in schema["components"]["schemas"]
    examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
    assert len(examples) >= 1
