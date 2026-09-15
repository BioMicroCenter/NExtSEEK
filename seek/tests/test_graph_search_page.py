"""The graph search page: Sample Search with its results served by graph_search.

``/seek/search/`` sends GET form data to the server-side engine (``seek/sample/search.py``)
and loads every matching row into the browser at once. ``/seek/graph/search/`` keeps that
page's layout and its Simple and Advanced tabs, but POSTs JSON to
``POST /nextseek_api/samples/graph_search/`` and pages in the database. It leaves out the
tabs that are not searches (Sample Retrieval, Sample Deletion) and the Delete button.

The page is rendered with the real template, so a template syntax error fails here.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import RequestFactory
from django.urls import resolve, reverse

import seek.views.search  # noqa: F401  -- so @patch can resolve the target

ROOT = Path(__file__).resolve().parents[2]
TYPES = json.dumps([{"id": 26, "title": "TIS", "group": ""}, {"id": 11, "title": "D.SEQ", "group": "D."}])


def _db(logged_in):
    db = MagicMock()
    db.getSeekLogin.return_value = ({"status": True, "username": "demo"} if logged_in
                                    else {"status": False, "err": "not logged in"})
    return db


def _get():
    req = RequestFactory().get("/seek/graph/search/")
    req.user = MagicMock()
    return req


@patch("seek.views.search.DBtable_sampletype")
@patch("seek.decorators.SeekDB")
def _render(mock_db, mock_types):
    mock_db.return_value = _db(True)
    mock_types.return_value.getSampleTypes.return_value = TYPES
    resp = seek.views.search.graphSearch(_get())
    return resp, resp.content.decode()


def test_the_page_is_routed_at_seek_graph_search():
    assert reverse("graphSearch") == "/seek/graph/search/"
    assert resolve("/seek/graph/search/").func.__name__ == "graphSearch"


@patch("seek.decorators.SeekDB")
def test_a_logged_out_caller_goes_to_login_and_comes_back_here(mock_db):
    mock_db.return_value = _db(False)
    resp = seek.views.search.graphSearch(_get())
    assert resp.status_code == 302
    assert resp.url == "/login/?next=/seek/graph/search/"


def test_the_page_renders_with_the_sample_type_options():
    resp, body = _render()
    assert resp.status_code == 200
    assert '"D.SEQ"' in body


def test_results_come_from_graph_search_not_the_server_side_engine():
    _, body = _render()
    assert "/nextseek_api/samples/graph_search/" in body
    assert "/seek/searchAdvanced/" not in body
    assert "/seek/samples/searching/" not in body


def test_both_search_tabs_are_there_and_the_non_search_tabs_are_not():
    _, body = _render()
    assert 'title="Sample Search"' in body
    assert 'title="Advanced Sample Search"' in body
    assert "Sample Retrieval" not in body
    assert "Sample Deletion" not in body
    assert "/seek/samples/delete/" not in body


def test_the_tab_container_keeps_the_id_the_layout_css_is_scoped_to():
    """nextseek.css scopes its layout fix to #search_tab; without it the grid collapses."""
    _, body = _render()
    assert 'id="search_tab"' in body


def test_the_simple_tab_never_calls_a_combobox_before_easyui_has_built_it():
    """EasyUI auto-selects an item in the sample type box while it parses the page, which
    fires its onSelect before the attribute and rule boxes below it exist. A handler that
    calls .combobox('clear') on one of those throws, and the throw aborts EasyUI's parse, so
    no tab and no grid on the page is ever built (seen live on 2026-09-15). Every reset goes
    through gsComboReset, which checks the box exists first."""
    simple = (ROOT / "seek/templates/pages/graphSearch_simple.embed.html").read_text()
    assert "function gsComboReset(" in simple
    assert "data('combobox')" in simple
    assert simple.count(".combobox('clear')") == 1, "call combobox('clear') only inside gsComboReset"


def test_the_sidebar_links_the_page_next_to_sample_search_for_every_user():
    nav = (ROOT / "themes/NextSeek/templates/nav.embed.html").read_text()
    admin_gate = nav.index("{% if request.user.is_superuser %}")
    assert nav.index('href="/seek/search/"') < nav.index('href="/seek/graph/search/"') < admin_gate
