"""JSON handed to an inline <script> must not be able to close it.

The search and attribute pages inject `report.type_options` (already JSON text
built from sample type titles) into a <script> block. The titles are user data,
so the text goes through `json_script_safe`, never `|safe`.
"""
import json
import pathlib
import re

import pytest
from django.template.loader import render_to_string

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVIL = "</script><script>alert(1)</script>"
PAGES = ["pages/searchAdvanced_search.embed.html", "pages/samples_search.embed.html"]
SINK_FILES = [
    "seek/templates/pages/samples_search.embed.html",
    "seek/templates/pages/searchAdvanced_search.embed.html",
    "seek/templates/sampleAttributes.html",
]


@pytest.mark.parametrize("page", PAGES)
def test_title_cannot_close_the_script(page):
    options = json.dumps([{"id": 1, "title": EVIL}])
    out = render_to_string(page, {"report": {"type_options": options}})
    assert "</script><script>alert(1)" not in out
    assert "</script" in out  # the page's own closing tag is still there


def test_no_template_marks_option_json_safe():
    for rel in SINK_FILES:
        text = (ROOT / rel).read_text()
        assert not re.search(r"type_options\s*\|\s*safe", text), rel
        assert not re.search(r"attribute_types_options\s*\|\s*safe", text), rel


def test_filter_escapes_and_stays_the_same_json():
    from seek.templatetags.json_script_safe import json_script_safe

    raw = json.dumps([{"t": EVIL + "&  "}], ensure_ascii=False)
    out = json_script_safe(raw)
    for ch in "<>&  ":
        assert ch not in out
    assert json.loads(out) == json.loads(raw)


@pytest.mark.parametrize("value, expected", [(None, "null"), ("", "null"), ({"a": "<"}, '{"a": "\\u003c"}')])
def test_filter_non_string_values_do_not_crash(value, expected):
    from seek.templatetags.json_script_safe import json_script_safe

    assert json_script_safe(value) == expected


# --- other JSON-in-script sinks: a hostile value must not close the script ---
def _render(name, ctx):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    return render_to_string(name, ctx, request=request)


_people = json.dumps({"1": [{"id": 1, "title": EVIL}]})
_labs = json.dumps([{"id": 0, "title": "x"}, {"id": 1, "title": EVIL}])
SINKS = {
    "pages/batch_upload.embed.html": {"report": {"lab_options": _labs, "all_lab_users": _people}},
    "dataFileUpload.html": {"report": {"lab_options": _labs, "all_lab_users": _people}},
    "clades.html": {"clades": [{"id": 1, "title": EVIL}], "stc": json.dumps([{"id": 1, "title": EVIL}])},
    "internal_assays.html": {
        "internal_assays": json.dumps([{"id": 1, "title": EVIL}]),
        "assay_associations": json.dumps([{"id": 1, "title": EVIL}]),
    },
}


@pytest.mark.parametrize("name", list(SINKS))
def test_hostile_value_cannot_close_the_script(name):
    out = _render(name, SINKS[name])
    assert "</script><script>alert(1)" not in out


def test_no_script_json_marked_safe_with_spaces():
    import re

    for rel in SINK_FILES + [
        "seek/templates/pages/batch_upload.embed.html",
        "seek/templates/dataFileUpload.html",
        "seek/templates/clades.html",
        "seek/templates/internal_assays.html",
    ]:
        text = (ROOT / rel).read_text()
        assert not re.search(r"(type_options|attribute_types_options|lab_options|all_lab_users|clades|stc|internal_assays|assay_associations|my_project_ids)[^}]*\|\s*safe", text), rel
