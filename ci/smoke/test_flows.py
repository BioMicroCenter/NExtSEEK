"""The five functional flows, driven through a real browser.

A status code cannot tell you a page is working. These drive the actual UI and,
with --strict-console, fail on uncaught console errors. That check is the only
thing here that catches the class of defect where a page returns 200 while its
JavaScript is broken.

Nothing in this file writes to the database. The upload flow stops at validate,
which runs the pipeline through TRANSFORM and returns before any INSERT.
"""
from __future__ import annotations

import re

import pytest

from ci.smoke.conftest import SMOKE_SEARCH_TERM

pytestmark = pytest.mark.flow

UID_RE = re.compile(r"\A([A-Z]\.)?[A-Z]{2,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?\Z")

# Both search boxes of the Sample Search page, and the graph search flow below, POST here.
GRAPH_SEARCH_PATH = "/nextseek_api/samples/graph_search/"


def _is_graph_search(response) -> bool:
    return (response.url.split("?", 1)[0].endswith(GRAPH_SEARCH_PATH)
            and response.request.method == "POST")


@pytest.fixture(scope="session")
def a_sample(discovered):
    """A real sample, discovered at run time. Never hard-code an id.

    This used to make its own searchAdvanced request. It now reads the one the
    conftest `discovered` fixture already makes for the whole suite: two copies of
    the same query could disagree about which sample is under test, and a flow
    failing on a different row from the one T0 swept is a bad half-hour.
    """
    if not discovered.get("sample_id"):
        pytest.skip("no sample could be discovered in this environment")
    return {"id": discovered["sample_id"], "uid": discovered.get("sample_uid") or ""}


# --------------------------------------------------------------------------- #
# Flow A: advanced search
# --------------------------------------------------------------------------- #

@pytest.mark.profiles("local", "dev")
def test_advanced_search_returns_rendered_results(page, base_url):
    """The daily driver, whose Advanced box searches through graph_search.

    Local and dev only: the box sends its search as a POST to graph_search, which the
    prod guard aborts at the network layer (see test_upload_validate_reports_a_result),
    as it does for graph_search's own Route.

    Two things here are easy to get wrong and are deliberate:

    * #input_searchText is an EasyUI *multiline* textbox. On init EasyUI hides it
      and injects a real <textarea> into a sibling span, so fill() on the id times
      out. Driving #input_searchValue and clicking Add is the real user path and
      goes through searchAdd(), which calls textbox('setText').
    * Row assertions read the jQuery data API, not the DOM. EasyUI hides the
      original <table> and renders rows into a sibling div, and the grid is built
      while its tab is hidden, so toBeVisible() is unreliable where count() is not.
    """
    page.goto(f"{base_url}/seek/search/?tab=advanced", wait_until="domcontentloaded",
              timeout=120_000)
    page.wait_for_function(
        "() => window.jQuery && !!jQuery('#advanced_dgtable').data('datagrid')",
        timeout=60_000,
    )
    page.click('#search_tab .tabs-header span.tabs-title:has-text("Advanced Sample Search")')

    page.fill("#input_searchValue", SMOKE_SEARCH_TERM)
    page.click('a.easyui-linkbutton[onclick="searchAdd()"]')
    assert page.evaluate("() => $('#input_searchText').textbox('getText')") == SMOKE_SEARCH_TERM

    with page.expect_response(_is_graph_search, timeout=180_000) as got:
        # Scope by onclick: the simple tab has its own a.ns-btn-search.
        page.click('a.ns-btn-search[onclick*="searchAdvanced"]')
    assert got.value.status == 200, f"graph_search answered {got.value.status}"

    page.wait_for_selector("div.window-mask", state="hidden", timeout=60_000)

    reported = page.inner_text("#numberSamplesFound").strip()
    assert reported.isdigit() and int(reported) > 0, f"result count was {reported!r}"
    assert int(reported) == got.value.json()["total"], (
        f"the page reports {reported} samples; graph_search answered {got.value.json()['total']}"
    )

    # graph_search pages in the database and the grid holds one page (#advanced_pager
    # asks for the others), not the whole result set. Never assert equality here,
    # and never assert a literal count: totals are environment-specific.
    n_rows = page.evaluate("() => $('#advanced_dgtable').datagrid('getRows').length")
    assert 0 < n_rows <= int(reported), (
        f"grid holds {n_rows} rows against a reported total of {reported}"
    )

    href = page.get_attribute("#advanced_dgtable_wrapper td[field='uid'] a, "
                              "div.datagrid-view2 td[field='uid'] a", "href")
    assert href and re.fullmatch(r"/seek/sample/id=\d+/", href), (
        f"first result links to {href!r}, not a sample page"
    )


# --------------------------------------------------------------------------- #
# Flow B: Nessie
# --------------------------------------------------------------------------- #

def test_nessie_loads_and_is_wired(page, base_url):
    """Proves the chat page is live without spending a cent.

    Sending a message costs a real model call, so this exploits a real feature
    instead: the input hydrates from the ?q= parameter on mount. Only live
    JavaScript can produce that value, which also covers the silent failure mode
    where the Vite template tag returns an empty string and renders an empty div
    at HTTP 200 with a clean console.
    """
    page.goto(f"{base_url}/seek/assistant/?q=playwright%20smoke",
              wait_until="domcontentloaded", timeout=120_000)

    bundle = page.locator('script[src*="/static/js/chat_assistant/assets/main.embedded-"]')
    assert bundle.count() == 1, (
        "the chat bundle script tag is missing. vite_assets returns an empty "
        "string when its manifest or entry key is absent, with no exception and "
        "no console error."
    )
    root = page.locator("#chat-assistant-root")
    root.wait_for(state="attached", timeout=60_000)
    chat_input = page.get_by_test_id("chat-input")
    chat_input.wait_for(state="visible", timeout=60_000)
    assert chat_input.input_value() == "playwright smoke", (
        "the input did not hydrate from ?q=, so the bundle is not running"
    )
    assert page.get_by_test_id("send-button").is_enabled()


def test_nessie_send_issues_the_expected_request_without_paying(page, base_url):
    """Abort the call before it leaves the browser, then assert what would have
    been sent. The client catches the rejection and appends a system message, so
    nothing crashes."""
    page.route("**/nextseek_api/cc-assistant/query/async/", lambda route: route.abort())
    page.goto(f"{base_url}/seek/assistant/?q=playwright%20smoke",
              wait_until="domcontentloaded", timeout=120_000)
    page.get_by_test_id("chat-input").wait_for(state="visible", timeout=60_000)

    # Match on pathname: 'assistant/query/async' also matches the legacy route.
    with page.expect_request(
        lambda r: r.url.endswith("/nextseek_api/cc-assistant/query/async/")
        and r.method == "POST",
        timeout=60_000,
    ) as got:
        page.get_by_test_id("send-button").click()
    body = got.value.post_data_json
    assert body["query"] == "playwright smoke"
    assert body.get("mode") == "standard"


# --------------------------------------------------------------------------- #
# Flow C: sample page
# --------------------------------------------------------------------------- #

def test_sample_page_populates(page, base_url, a_sample):
    resp = page.goto(f"{base_url}/seek/sample/id={a_sample['id']}/",
                     wait_until="domcontentloaded", timeout=180_000)
    # Asserted separately so a SEEK Rails failure is distinguishable from a UI
    # regression: this view makes a blocking, un-timed-out call to SEEK.
    assert resp is not None and resp.status == 200, (
        f"sample page returned {resp.status if resp else 'nothing'}"
    )
    rows = page.locator("table.TFtable tr")
    rows.first.wait_for(state="attached", timeout=60_000)
    assert rows.count() > 0, (
        "the attribute table is empty. A bogus-but-numeric id also renders 200 "
        "with an empty table, so this is the assertion that matters."
    )
    if a_sample["uid"]:
        assert UID_RE.fullmatch(a_sample["uid"]), f"discovered UID {a_sample['uid']!r} is malformed"
        assert a_sample["uid"] in page.content(), "the sample's UID does not appear on its own page"


# --------------------------------------------------------------------------- #
# Flow D: upload (validate only, never start)
# --------------------------------------------------------------------------- #

def _open_upload_page(page, base_url):
    page.goto(f"{base_url}/seek/samples/upload/", wait_until="domcontentloaded",
              timeout=240_000)  # this view calls SEEK once per institution and per person
    page.wait_for_selector("#sample_validation_file", state="attached", timeout=60_000)
    page.wait_for_function(
        "() => window.jQuery && ($('#validate_project_id').combobox('getData')||[]).length > 0",
        timeout=120_000,
    )


def test_upload_is_blocked_when_no_file_is_chosen(page, base_url):
    """Free: nothing is sent, so this costs one page load.

    The file input carries `required` (batch_upload.embed.html:18), so the browser
    blocks submission with native constraint validation before any JavaScript
    runs. That makes the script's own "Select a sample sheet to validate." guard
    unreachable through the UI, so asserting on that string would fail even
    though the behaviour is correct. Assert what actually happens.
    """
    _open_upload_page(page, base_url)
    page.click('button[type="submit"][form="sample_validation"]')
    blocked = page.evaluate(
        "() => document.querySelector('#sample_validation_file').validity.valueMissing"
    )
    assert blocked, "an empty file input did not block submission"
    log = page.locator("#messages").input_value()   # a <textarea>: .value, not textContent
    assert log.strip() == "", f"a request appears to have been made: {log!r}"


@pytest.mark.profiles("local", "dev")
def test_upload_validate_reports_a_result(page, base_url, request):
    """Drives the real validate call.

    Validation is free and writes nothing: no LLM, no Celery, no INSERT. It does
    take a MySQL advisory lock for UID generation, so on a shared box it can
    contend briefly with somebody's live upload.

    Not run under prod, and the reason is the SHAPE of the request rather than its
    effect. This is one of the two flows whose page issues a POST (Flow E, Graph
    Search, is the other), and the prod guard aborts every non-GET at the network
    layer -- correctly. The abort is silent to the page, so `expect_response` below
    would sit out its own five-minute timeout and report red for a rule the suite
    had just enforced. `profiles` is honoured by pytest_collection_modifyitems in
    the conftest; the other flows are GET-only and stay on prod.

    NEVER click button[form="sample_upload"]. That is /batch-upload/start/, a real
    Celery job that writes to MySQL and neo4j.
    """
    fixture = (
        request.config.rootpath.parent.parent
        / "nextseek_api/batch_upload/tests/fixtures/wave3_default_mode.xlsx"
    )
    if not fixture.is_file():
        pytest.skip(f"fixture not present: {fixture}")

    _open_upload_page(page, base_url)
    pid = page.evaluate(
        "() => { const d = $('#validate_project_id').combobox('getData');"
        "$('#validate_project_id').combobox('setValue', d[0].id); return String(d[0].id); }"
    )
    assert page.input_value("#validate_project_id") == pid

    page.set_input_files("#sample_validation_file", str(fixture))
    with page.expect_response(
        lambda r: "/nextseek_api/batch-upload/validate/" in r.url
        and r.request.method == "POST",
        timeout=300_000,
    ) as got:
        page.click('button[type="submit"][form="sample_validation"]')

    resp = got.value
    # 200 even for an invalid sheet: validity lives in the body, never in the status.
    assert resp.status == 200, f"validate returned {resp.status}"
    body = resp.json()
    assert "valid" in body, f"no 'valid' flag in the response: {sorted(body)[:8]}"

    page.wait_for_selector("div.window-mask", state="hidden", timeout=60_000)
    log = page.locator("#messages").input_value()
    assert re.search(r"^(PASSED|FAILED) - ", log, re.M), f"no verdict rendered:\n{log[:500]}"
    assert ("PASSED" in log) == bool(body["valid"]), (
        "the rendered verdict disagrees with the response body"
    )


# --------------------------------------------------------------------------- #
# Flow E: Sample Search's Simple box, through graph_search
# --------------------------------------------------------------------------- #

@pytest.mark.profiles("local", "dev")
def test_simple_search_asks_graph_search_for_one_sample_type(page, base_url, discovered):
    """The Simple box sends one sample type to graph_search and the grid takes one page.

    Flow A drives the Advanced box; this drives the other one, whose body names the
    type by title and carries no terms. T0 already GETs the page, which proves a status
    and no bounce to /login/; with --strict-console a script error fails this, the
    class of defect where the page renders and cannot search.

    Local and dev only, like graph_search's Route: the search is a POST, which the prod
    guard aborts at the network layer (see test_upload_validate_reports_a_result).

    The type is the discovered sample's, which the smoke account can see, else the
    first the box offers. It is chosen with combobox('select'), the user's path, which
    loads the type's attributes and leaves the attribute on 'none', so the search is for
    every sample of the type. No count is asserted: a type the graph holds none of is a
    working search. The pager must report graph_search's total, and the grid hold one
    page of it.
    """
    page.goto(f"{base_url}/seek/search/?tab=simple", wait_until="domcontentloaded",
              timeout=120_000)
    page.wait_for_function(
        "() => window.jQuery && !!jQuery('#simple_dgtable').data('datagrid')"
        " && !!jQuery('#simple_pager').data('pagination')",
        timeout=60_000,
    )
    type_id = page.evaluate(
        """(wanted) => {
            var ids = (window.type_options || []).map(function (t) { return String(t.id); });
            return ids.indexOf(wanted) >= 0 ? wanted : (ids[0] || null);
        }""",
        discovered.get("sample_type_id") or "",
    )
    if not type_id:
        pytest.skip("the Simple box offers no sample type on this box")
    page.evaluate("(id) => $('#simple_sample_type').combobox('select', id)", type_id)

    with page.expect_response(_is_graph_search, timeout=180_000) as got:
        # Scope by onclick: the Advanced tab has its own a.ns-btn-search.
        page.click('a.ns-btn-search[onclick="simple_searchSamples()"]')
    resp = got.value
    assert resp.status == 200, f"graph_search answered {resp.status}"
    sent = resp.request.post_data_json
    assert sent.get("sampletype") and sent.get("filter_searchText") == "", (
        f"the Simple box sent {sent!r}, not one sample type"
    )

    page.wait_for_selector("div.window-mask", state="hidden", timeout=60_000)
    total = resp.json()["total"]
    shown = page.evaluate("() => $('#simple_pager').pagination('options').total")
    assert shown == total, f"the pager reports {shown} samples; graph_search answered {total}"
    n_rows = page.evaluate("() => $('#simple_dgtable').datagrid('getRows').length")
    size = page.evaluate("() => $('#simple_pager').pagination('options').pageSize")
    assert n_rows == min(total, size), (
        f"the grid holds {n_rows} rows of {total}, at a page size of {size}"
    )
