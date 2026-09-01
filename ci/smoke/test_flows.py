"""The four functional flows, driven through a real browser.

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

pytestmark = pytest.mark.flow

UID_RE = re.compile(r"\A([A-Z]\.)?[A-Z]{2,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?\Z")


@pytest.fixture(scope="session")
def a_sample(web, base_url):
    """Discover a real sample at run time. Never hard-code an id.

    Uses the endpoint the search page itself calls. Sample-type ids and row
    counts are deployment-specific, so this returns whatever the environment
    actually has and skips when it has nothing.
    """
    r = web.get(
        f"{base_url}/seek/searchAdvanced/",
        params={
            "sampletype_id": "", "attribute": "none", "filter_logic": "AND",
            "filter_searchValue": "", "filter_searchText": "Uterus",
            "filter_matchType": "PARTIAL",
        },
        timeout=180,
    )
    assert r.status_code == 200, f"sample discovery failed: {r.status_code}"
    rows = r.json().get("rows") or []
    if not rows:
        pytest.skip("no samples matched the discovery query in this environment")
    row = rows[0]
    uid = re.sub(r"<[^>]+>", "", str(row.get("uid", ""))).strip()
    return {"id": row["id"], "uid": uid}


# --------------------------------------------------------------------------- #
# Flow A: advanced search
# --------------------------------------------------------------------------- #

def test_advanced_search_returns_rendered_results(page, base_url):
    """The daily driver.

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

    page.fill("#input_searchValue", "Uterus")
    page.click('a.easyui-linkbutton[onclick="searchAdd()"]')
    assert page.evaluate("() => $('#input_searchText').textbox('getText')") == "Uterus"

    with page.expect_response(
        lambda r: "/seek/searchAdvanced/" in r.url, timeout=180_000
    ) as got:
        # Scope by onclick: the simple tab has its own a.ns-btn-search.
        page.click('a.ns-btn-search[onclick*="searchAdvanced"]')
    assert got.value.status == 200

    page.wait_for_selector("div.window-mask", state="hidden", timeout=60_000)

    reported = page.inner_text("#numberSamplesFound").strip()
    assert reported.isdigit() and int(reported) > 0, f"result count was {reported!r}"

    # The grid paginates at pageSize 100 (searchAdvanced_stable.embed.html:194-195),
    # so it holds one page, not the whole result set. Never assert equality here,
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


def test_upload_validate_reports_a_result(page, base_url, request):
    """Drives the real validate call.

    Validation is free and writes nothing: no LLM, no Celery, no INSERT. It does
    take a MySQL advisory lock for UID generation, so on a shared box it can
    contend briefly with somebody's live upload.

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
