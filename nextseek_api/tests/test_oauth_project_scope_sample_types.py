"""Project scoping for the sample-type graph endpoint (#16, sub-project 2).

The same defect as ``_caller_seek_project_ids``, in a second place: the scope
helper took a ``basic_tuple``, which an OAuth caller cannot produce, so it
reported "no SEEK credentials" and returned an empty row set. The endpoint then
answered 200 with nothing in it.

Worth keeping distinct from a 401 in your head. This helper's contract is
*fail closed* -- "if the caller's projects cannot be resolved, nothing is
returned rather than everything" -- which is right, and is exactly why the bug
was invisible: the failure mode of a missing credential and the failure mode of
"you genuinely have no projects" produce identical responses.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.services.sample_types import _scope_graph_rows_to_caller_projects

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


def _request(is_superuser=False):
    return SimpleNamespace(
        META={}, session={}, method="GET",
        user=SimpleNamespace(is_authenticated=True, is_superuser=is_superuser, pk=7),
    )


def _seekdb_with_projects(*ids):
    return SimpleNamespace(getCurrentUser=lambda: {
        "data": {"relationships": {"projects": {"data": [{"id": i} for i in ids]}}}
    })


def _patch_seekdb(value):
    return patch("nextseek_api.services.sample_types.seekdb_for_caller",
                 return_value=value)


def test_a_superuser_is_not_scoped(oauth_on):
    """Unchanged, and checked first so the credential lookup never runs for
    them."""
    rows = [{"uuid": "S.1"}]
    with _patch_seekdb(None) as seekdb:
        assert _scope_graph_rows_to_caller_projects(_request(is_superuser=True), rows) == rows
    seekdb.assert_not_called()


def test_a_caller_with_no_credential_sees_nothing(oauth_on):
    """Fails closed. Still reachable for a DRF-token client, which resolves
    neither a Basic pair nor an OAuth token."""
    with _patch_seekdb(None):
        assert _scope_graph_rows_to_caller_projects(_request(), [{"uuid": "S.1"}]) == []


def test_a_seek_failure_sees_nothing(oauth_on):
    def _boom():
        raise RuntimeError("SEEK is down")

    with _patch_seekdb(SimpleNamespace(getCurrentUser=_boom)):
        assert _scope_graph_rows_to_caller_projects(_request(), [{"uuid": "S.1"}]) == []


def test_a_caller_whose_projects_resolve_empty_sees_nothing(oauth_on):
    with _patch_seekdb(_seekdb_with_projects()):
        assert _scope_graph_rows_to_caller_projects(_request(), [{"uuid": "S.1"}]) == []


def test_an_oauth_caller_reaches_the_project_lookup(oauth_on):
    """The regression guard. Before this, an OAuth caller could not get past
    the credential check at all, so the SQL below never ran and the endpoint
    returned an empty -- but entirely successful -- response.

    The DB query itself is stubbed; what is asserted is that a caller with no
    password now gets *to* it.
    """
    rows = [{"uuid": "S.1"}, {"uuid": "S.2"}]
    cursor = MagicMock()
    cursor.fetchall.return_value = [("S.1",)]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with _patch_seekdb(_seekdb_with_projects(3, 5)), \
         patch.dict("nextseek_api.services.sample_types.connections",
                    {"seek": connection}, clear=False):
        result = _scope_graph_rows_to_caller_projects(_request(), rows)

    assert result == [{"uuid": "S.1"}]
    bound = cursor.execute.call_args.args[1]
    assert "3" in bound and "5" in bound
