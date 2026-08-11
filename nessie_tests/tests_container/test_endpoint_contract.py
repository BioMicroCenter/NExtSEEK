"""In-process DRF contract test for the REAL top-level router endpoint.

This proves the http_driver's exact request body shape is accepted by the live
view (`CCAssistantViewSet.query_async`) and that `QueryRequest`'s
``extra="forbid"`` rejects stray keys -- WITHOUT running a paid turn. The view
spawns the actual pipeline in a daemon thread and returns HTTP 202 immediately;
that background thread may error without live services, and that is fine: this
test asserts only the POST contract (202 / 422), never turn completion.

The `auth_client` / `mock_assistant_permission` fixtures live in
``nextseek_api/conftest.py``. That conftest is a *sibling* of ``nessie_tests/``
(not an ancestor), so pytest does not auto-load it when this test is collected
under ``nessie_tests/tests_container/``. We therefore import the real fixtures
explicitly -- the standard pytest cross-directory fixture-reuse pattern. This
reuses the genuine app fixtures (no re-implementation); `auth_client` pulls in
`api_user` + `api_client`, which is why those are imported too.
"""
import pytest

from nextseek_api.conftest import (  # noqa: F401  (re-exported pytest fixtures)
    api_client,
    api_user,
    auth_client,
    mock_assistant_permission,
)


@pytest.mark.django_db
def test_query_async_accepts_minimal_body_and_returns_task(auth_client, mock_assistant_permission):
    # QueryRequest is extra="forbid"; the exact body the driver sends must validate.
    resp = auth_client.post("/nextseek_api/cc-assistant/query/async/",
                            {"query": "Find mice treated with NDMA.", "mode": "standard"}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert "task_id" in body and "session_id" in body


@pytest.mark.django_db
def test_stray_key_is_rejected(auth_client, mock_assistant_permission):
    resp = auth_client.post("/nextseek_api/cc-assistant/query/async/",
                            {"query": "x", "mode": "standard", "bogus": 1}, format="json")
    assert resp.status_code == 422
