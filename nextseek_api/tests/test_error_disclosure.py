"""REST errors carry a reference, not the database's or the graph's text."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory
from neo4j.exceptions import Neo4jError

import seek.views.timeline as timeline_views

_VIEWS = "nextseek_api.views"


def test_tree_neo4j_error_hides_the_host():
    from nextseek_api.views import SampleTreeViewSet

    driver = MagicMock()
    driver.__enter__.return_value.execute_query.side_effect = Neo4jError("bolt://neo4j-internal:7687 refused")
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=False)
    request.session = {}
    vs = SampleTreeViewSet()
    vs.format_kwarg = None
    vs.request = request
    with patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42"), \
            patch(f"{_VIEWS}._caller_seek_project_ids", return_value=["5"]), \
            patch(f"{_VIEWS}._samples_visible_to_projects", return_value={"42"}), \
            patch(f"{_VIEWS}.GraphDatabase") as gdb:
        gdb.driver.return_value = driver
        resp = vs.get_tree(request, uid="42")
    assert resp.status_code == 503
    assert "neo4j-internal" not in resp.data["detail"]
    assert "ref " in resp.data["detail"]


def test_nhp_info_error_hides_the_database_text():
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
    request.session = {}
    with patch.object(timeline_views, "_callerProjectIds", return_value=None), \
            patch.object(timeline_views, "save_nhp_info_to_json", side_effect=Exception("mysql-host-x")):
        resp = timeline_views.nhp_info(request, "FLY-1")
    assert resp.status_code == 500
    assert "mysql-host-x" not in resp.data["detail"]
    assert "ref " in resp.data["detail"]
