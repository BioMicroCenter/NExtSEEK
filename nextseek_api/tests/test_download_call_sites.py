"""No template Django actually renders may still post to a legacy download endpoint.

Orphans are excluded deliberately: nothing includes searchAdvanced_rtable or
searchAdvanced_tree, sampleSearch.html's render is commented out at
seek/views.py:412 and the view redirects instead, sampleDeletion.html has no
view, and .bk is a backup. See docs/sample-download-workflow.md.
"""

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "seek" / "templates"

LEGACY = ("/seek/samples/download/", "/seek/admin/retrieve/")

ORPHANS = [
    "pages/searchAdvanced_rtable.embed.html",
    "pages/searchAdvanced_tree.embed.html",
    "sampleSearch.html",
    "sampleDeletion.html",
    "pages/samples_stable.embed.html.bk",
]

LIVE = [
    "pages/samples.embed.html",
    "searchAdvanced.html",
    "pages/searchAdvanced_stable.embed.html",
    "pages/samples_stable.embed.html",
    "pages/searchAdvanced_newretrieval.embed.html",
    "newSearch.html",
    "pages/samples_new_stable.embed.html",
    "pages/searchAdvanced_new_stable.embed.html",
]

# Where the helper script tag must appear. samples.embed.html carries its own
# because samples.html only wraps it; the other two are the rendered pages.
LOADERS = ["pages/samples.embed.html", "searchAdvanced.html", "newSearch.html"]


@pytest.mark.parametrize("name", LIVE)
def test_live_template_has_no_legacy_download_endpoint(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
    for endpoint in LEGACY:
        assert endpoint not in text, f"{name} still references {endpoint}"


@pytest.mark.parametrize("name", LOADERS)
def test_the_helper_script_is_loaded_where_downloads_happen(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
    assert "ns_sample_download.js" in text, f"{name} does not load the helper"


@pytest.mark.parametrize("name", ORPHANS)
def test_orphan_list_still_matches_reality(name):
    """If someone wires an orphan back up, this test should be revisited."""
    assert (TEMPLATES / name).exists(), f"{name} vanished; update this test"


# Wrappers that exist only to call the helper. Included fragments reference these
# rather than the helper itself, because the handler lives in the parent template.
WRAPPERS = ("downloadSamples", "downloadSamples0", "simple_downloadSamples", "retrieveSamples")


def test_every_wrapper_that_is_defined_calls_the_helper():
    """A wrapper that stopped calling the helper would silently break its button,
    which the legacy-endpoint check alone cannot catch."""
    for name in LIVE:
        text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
        for wrapper in WRAPPERS:
            if f"function {wrapper}(" not in text:
                continue
            assert "nsDownloadSamples" in text, (
                f"{name} defines {wrapper}() but never calls nsDownloadSamples"
            )


def test_every_download_control_routes_somewhere_real():
    """Each 'Download samples' control must invoke the helper or one of its wrappers."""
    reachable = ("nsDownloadSamples",) + WRAPPERS
    for name in LIVE:
        text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
        if "Download samples" not in text and "Download All Samples" not in text:
            continue
        assert any(fn in text for fn in reachable), (
            f"{name} has a download control wired to nothing"
        )


def test_the_immport_export_did_not_become_a_download():
    """simple_downloadSamples used to be overloaded on its url argument, so the
    'Export samples to Import' button shared it. Splitting them must not have
    pointed that button at the download path."""
    text = (TEMPLATES / "pages/samples_stable.embed.html").read_text(encoding="utf-8", errors="replace")
    assert "simple_exportSamples($('#simple_dgtable'), '/seek/samples/export/')" in text
    assert "function simple_exportSamples(" in text
