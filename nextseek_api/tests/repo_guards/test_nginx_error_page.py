"""The page nginx shows while the app restarts must still read as a failure.

`docker/nginx-optional/error-pages.conf` replaces nginx's bare 502 with a short
self-reloading page. `ci/smoke/assertions.py` `check_gateway` tells a dead
stack (a non-JSON 502 from nginx) from an application 502 (a JSON envelope),
and health probes count any 502 as down. Both hold only while the page keeps
the 502 status and an HTML body, so these guards pin that, and pin that git
tracks the file at all (its folder ignores `*.conf`).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONF = REPO_ROOT / "docker" / "nginx-optional" / "error-pages.conf"


def _directives() -> str:
    return "\n".join(l for l in CONF.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_error_page_keeps_the_502_status():
    text = _directives()
    pages = re.findall(r"^\s*error_page\s+([^;]+);", text, re.M)
    assert pages == ["502 @nextseek_restarting"], pages
    assert "=" not in pages[0], "error_page must not rewrite the status"
    assert re.search(r"return 502 '", text), "the named location must answer 502"


def test_error_page_body_is_html_and_safe_for_nginx():
    text = _directives()
    assert "default_type text/html;" in text
    body = re.search(r"return 502 '(.*)';", text, re.S).group(1)
    assert body.startswith("<!doctype html>")
    assert "$" not in body, "nginx would expand a dollar sign as a variable"
    assert "'" not in body, "a single quote would end nginx's string"


def test_error_page_is_tracked_despite_the_folder_ignoring_conf_files():
    lines = [l.strip() for l in (CONF.parent / ".gitignore").read_text().splitlines()]
    assert "*.conf" in lines
    assert "!error-pages.conf" in lines[lines.index("*.conf"):], "the negation must follow *.conf"
