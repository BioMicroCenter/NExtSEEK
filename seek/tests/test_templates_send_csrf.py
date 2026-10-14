"""Every page or script that writes to the API must send the CSRF header.

Plain file reads, no Django. A write is a fetch/$.ajax/$.post/XMLHttpRequest/axios
call whose method is not a literal GET or HEAD. The header may be spelled in any
case. The token must sit inside the same call: the text from the call up to the
next call or 600 characters, whichever is shorter.

Covered: seek/templates, themes/NextSeek/templates, static/js (walked directly).
Skipped by name: node_modules, *.min.js, jquery*, easyui*, respond.min.js,
static/js/chat_assistant (the compiled bundle, own assertion below),
static/js/sample_timeline (vendored timeline assets).
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIRS = ["seek/templates", "themes/NextSeek/templates", "static/js"]
SKIP_DIRS = {"node_modules", "chat_assistant", "sample_timeline"}
SKIP_NAME = re.compile(r"(\.min\.js$|^jquery|^easyui|^respond)", re.I)
EXTS = {".html", ".js"}

CALL = re.compile(r"(?:(?<![\w$.])|(?<=window\.))(fetch\s*\(|\$\.ajax\s*\(|\$\.(?:post|put|patch|delete)\s*\(|axios(?:\.\w+)?\s*\(|new\s+XMLHttpRequest)")
READ_ONLY = re.compile(r"""(?:method|type)\s*:\s*['"](?:GET|HEAD)['"]""", re.I)
METHOD = re.compile(r"""(?:method|type)\s*:""", re.I)
TOKEN = re.compile(r"X-CSRFToken", re.I)
WINDOW = 600

# Writers known to lack the token (strict xfail until fixed): none.
KNOWN_MISSING = {}


def _files():
    for d in DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.suffix not in EXTS or not p.is_file():
                continue
            if SKIP_DIRS & set(p.relative_to(ROOT).parts) or SKIP_NAME.search(p.name):
                continue
            yield p


def _writers_without_token(text):
    bad = []
    for m in CALL.finditer(text):
        # The window ends at the next call, so a later call's token or GET cannot cover this one.
        nxt = CALL.search(text, m.end())
        win = text[m.start(): min(m.start() + WINDOW, nxt.start() if nxt else len(text))]
        head = m.group(1)
        if head.startswith("new"):
            writes = ".open(" in win and re.search(r"""open\(\s*['"](POST|PUT|PATCH|DELETE)""", win, re.I) is not None
        elif head.startswith("$.") and not head.startswith("$.ajax"):
            writes = True
        elif head.startswith("axios") and re.match(r"axios\.(get|head)", head):
            writes = False
        else:
            writes = (METHOD.search(win) is not None and not READ_ONLY.search(win)) or head.startswith("axios")
        if writes and not TOKEN.search(win):
            bad.append(text.count("\n", 0, m.start()) + 1)
    return bad


def _cases():
    out = []
    for p in _files():
        rel = str(p.relative_to(ROOT))
        text = p.read_text(errors="replace")
        if "/nextseek_api/" not in text and "API" not in text:
            continue
        out.append(pytest.param(rel, id=rel, marks=(
            [pytest.mark.xfail(strict=True, reason=KNOWN_MISSING[rel])] if rel in KNOWN_MISSING else [])))
    return out


@pytest.mark.parametrize("rel", _cases())
def test_every_writing_page_sends_the_token(rel):
    lines = _writers_without_token((ROOT / rel).read_text(errors="replace"))
    assert not lines, f"{rel}: write call(s) without X-CSRFToken at line(s) {lines}"


def test_chat_bundle_sends_the_token():
    bundle = list((ROOT / "static/js/chat_assistant/assets").glob("main.embedded-*.js"))
    assert bundle, "committed chat bundle not found"
    assert any(TOKEN.search(b.read_text(errors="replace")) for b in bundle)


# --- the guard itself, on synthetic snippets ---
@pytest.mark.parametrize("snippet", [
    "  $.post('/nextseek_api/x', {a: 1});",
    "window.fetch('/nextseek_api/x', {method: 'POST'});",
    "\t$.ajax({url: '/nextseek_api/x', type: 'POST'});",
    # a later call's token must not cover an earlier tokenless POST
    "fetch('/nextseek_api/x', {method: 'POST'});\nfetch('/nextseek_api/y', {method: 'GET', headers: {'X-CSRFToken': t}});",
    # a later call's GET must not make an earlier POST look read-only
    "fetch('/nextseek_api/x', {method: 'POST'});\nfetch('/nextseek_api/y', {method: 'GET'});",
])
def test_guard_flags_tokenless_writes(snippet):
    assert _writers_without_token(snippet)


def test_guard_accepts_tokened_writes():
    assert not _writers_without_token("fetch('/nextseek_api/x', {method: 'POST', headers: {'x-csrftoken': t}});")
