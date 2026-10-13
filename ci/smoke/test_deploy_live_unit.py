"""No-stack tests for test_deploy_live.py's helpers. No network, no credentials."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci import routes
from ci.smoke import deploy_live as dl


def test_the_committed_manifest_names_files_the_checkout_holds():
    files = dl.chat_bundle_files()
    assert files[0].startswith("assets/main.embedded-") and files[0].endswith(".js")
    for name in files:
        assert (dl.REPO_ROOT / dl.CHAT_BUNDLE_DIR / name).is_file(), name


def test_chat_bundle_files_reads_the_entry_js_then_its_css(tmp_path):
    manifest = tmp_path / dl.CHAT_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({dl.CHAT_ENTRY: {
        "file": "assets/main.embedded-X.js", "css": ["assets/main-Y.css"]}}))
    assert dl.chat_bundle_files(tmp_path) == ["assets/main.embedded-X.js", "assets/main-Y.css"]


def test_every_url_the_live_test_asks_for_is_declared_for_every_profile():
    for name in dl.chat_bundle_files():
        path = "/static/" + (dl.CHAT_BUNDLE_DIR / name).relative_to("static").as_posix()
        route = routes.match(path)
        assert route is not None and route.profiles == {"local", "dev", "prod"}, path
    for path in ("/seek/search/", "/seek/assistant/"):
        route = routes.match(path)
        assert route is not None and "prod" in route.profiles, path


FIXED = ('<select id="m_sampletype">\n<option value="0">All types</option>\n'
         '<option value="26">TIS</option>\n<option value="11">D.SEQ</option>\n</select>')
# The pre-fix page: one option per character of a JSON string, every one empty.
BROKEN = ('<select id="m_sampletype">\n<option value="0">All types</option>\n'
          + '<option value=""></option>\n' * 5 + '</select>')


def test_phone_type_options_of_the_fixed_page_are_all_valued():
    assert dl.phone_type_options(FIXED) == ["0", "26", "11"]


def test_phone_type_options_of_the_pre_fix_page_are_empty_but_the_first():
    options = dl.phone_type_options(BROKEN)
    assert options[0] == "0" and options[1:] == [""] * 5


def test_a_page_without_the_dropdown_is_none():
    assert dl.phone_type_options("<html></html>") is None


def test_template_lookup_failures_counts_the_debug_records():
    chunk = (b"DEBUG 2026-09-25 base Exception while resolving variable 'id' in template "
             b"'searchAdvanced.html'.\nTraceback ...\nINFO other\n"
             b"DEBUG Exception while resolving variable 'title' in template 'x'.\n")
    assert dl.template_lookup_failures(chunk) == 2
    assert dl.template_lookup_failures(b"INFO GET /seek/search/ 200\n") == 0


def test_appended_since_reads_only_the_new_bytes_and_all_of_a_rotated_file(tmp_path):
    log = tmp_path / "django.log"
    log.write_bytes(b"old line\n")
    offset = log.stat().st_size
    with log.open("ab") as f:
        f.write(b"new line\n")
    assert dl.appended_since(log, offset) == b"new line\n"
    log.write_bytes(b"rot\n")  # rotated: shorter than the offset
    assert dl.appended_since(log, offset) == b"rot\n"
