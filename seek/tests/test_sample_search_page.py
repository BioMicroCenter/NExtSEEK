"""The Sample Search page (/seek/search/) answers both of its search boxes from graph_search.

The page keeps its four tabs (Sample Search, Advanced Sample Search, Sample Retrieval,
Sample Deletion) and its toolbars, but the Simple and Advanced boxes, and the phone
form, POST JSON to ``POST /nextseek_api/samples/graph_search/`` and page in the
database instead of GETting ``/seek/samples/searching/`` and ``/seek/searchAdvanced/``,
which read every sample of the search into the server and the browser at once. The
request each box sends is built by pages/sampleSearch_core.embed.html, tested under
node by test_sample_search_js.py; this module checks the page wires it up.

The page is rendered with the real template, so a template syntax error fails here.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import RequestFactory
from django.urls import resolve, reverse

import seek.views.search  # noqa: F401  -- so @patch can resolve the target

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "seek" / "templates" / "pages"
TYPES = json.dumps([{"id": 26, "title": "TIS", "group": "Experimental type"},
                    {"id": 11, "title": "D.SEQ", "group": "Data type"}])
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


def _db(logged_in):
    db = MagicMock()
    db.getSeekLogin.return_value = ({"status": True, "username": "demo"} if logged_in
                                    else {"status": False, "err": "not logged in"})
    return db


def _get():
    req = RequestFactory().get("/seek/search/")
    req.user = MagicMock()
    return req


@patch("seek.views.search.DBtable_sampletype")
@patch("seek.decorators.SeekDB")
def _render(mock_db, mock_types):
    mock_db.return_value = _db(True)
    mock_types.return_value.getSampleTypes.return_value = TYPES
    resp = seek.views.search.searchAdvanced(_get())
    return resp, resp.content.decode()


def _pager_options(body, grid):
    """The data-options of the standalone pager that pages ``grid``."""
    m = re.search(r'<div id="%s_pager" class="easyui-pagination"[^>]*data-options="([^"]*)"' % grid, body)
    assert m, f"no standalone pager for {grid}"
    return m.group(1)


def _grid_options(body, grid):
    m = re.search(r'<table id="%s_dgtable"[^>]*data-options="([^"]*)"' % grid, body, re.S)
    assert m, f"no grid {grid}_dgtable"
    return m.group(1)


def test_the_page_is_routed_at_seek_search():
    assert reverse("searchAdvanced") == "/seek/search/"
    assert resolve("/seek/search/").func.__name__ == "searchAdvanced"


@patch("seek.decorators.SeekDB")
def test_a_logged_out_caller_goes_to_login_and_comes_back_here(mock_db):
    mock_db.return_value = _db(False)
    resp = seek.views.search.searchAdvanced(_get())
    assert resp.status_code == 302
    assert resp.url == "/login/?next=/seek/search/"


def test_the_page_renders_with_the_sample_type_options():
    resp, body = _render()
    assert resp.status_code == 200
    assert '"D.SEQ"' in body


def test_it_is_still_the_one_sample_search_page_with_its_four_tabs():
    _, body = _render()
    for title in ("Sample Search", "Advanced Sample Search", "Sample Retrieval", "Sample Deletion"):
        assert f'title="{title}"' in body, title
    # nextseek.css scopes its layout fix to #search_tab; without it the grids collapse.
    assert 'id="search_tab"' in body
    for button in ("simple_downloadSamples(", "simple_deleteSamples(", "simple_exportSamples(",
                   "showTimeline(", "downloadSamples0(", "deleteSamples(", "publishSamples(",
                   "sendSamples("):
        assert button in body, button


def test_both_boxes_and_the_phone_form_search_through_graph_search():
    _, body = _render()
    assert "var SampleSearchCore" in body
    assert "SampleSearchCore.simpleBody(" in body
    assert body.count("SampleSearchCore.queryBody(") == 2  # the Advanced box and the phone form
    assert "SampleSearchCore.searchUrl(" in body


def test_the_page_no_longer_calls_the_engines_that_read_every_matching_sample():
    """/seek/searchAdvanced/ and /seek/samples/searching/ load every matching row, with its
    json_metadata, into one response: the full scan that made the page an OOM vector."""
    _, body = _render()
    assert "/seek/searchAdvanced/" not in body
    assert "/seek/samples/searching/" not in body


def test_results_are_paged_in_the_database_by_standalone_pagers():
    """Each page of results is its own graph_search request. The grids keep their column
    filters, and datagrid-filter.js pages a grid with pagination on by slicing the rows it
    holds (myLoadFilter), which would empty every page after the first. So the grids hold
    one page with pagination off, and a pager of their own asks for the next page."""
    _, body = _render()
    for grid in ("simple", "advanced"):
        assert re.search(r"pagination\s*:\s*false", _grid_options(body, grid)), grid
        options = _pager_options(body, grid)
        assert "onSelectPage" in options, grid
        assert "pageList: [20, 50, 100, 500, 1000]" in options, grid  # graph_search caps page_size at 1000


def test_the_page_includes_the_core_once():
    _, body = _render()
    assert body.count("var SampleSearchCore = (function ()") == 1


def test_the_boxes_keep_the_request_shapes_graph_search_takes():
    """The Advanced box keeps its Add-to-query-box form, and the Simple box its
    type, attribute, operator, From and To inputs."""
    simple = (PAGES / "samples_search.embed.html").read_text()
    advanced = (PAGES / "searchAdvanced_search.embed.html").read_text()
    for field in ("simple_sample_type", "simple_sample_attribute", "filter_rule",
                  "input_valueFrom", "input_valueTo"):
        assert f'id="{field}"' in simple, field
    for field in ("input_searchValue", "input_logic", "advanced_sample_type", "input_searchText",
                  "numberSamplesFound", "numberSampleTypesFound"):
        assert f'id="{field}"' in advanced + (PAGES / "searchAdvanced_stable.embed.html").read_text(), field
    assert "function searchAdd()" in advanced
