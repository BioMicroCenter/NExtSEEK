"""The page nginx shows while the app restarts must still read as a failure.

`docker/nginx-optional/error-pages.conf` replaces nginx's bare 502 with a short
page and a Reload link. `ci/smoke/assertions.py` `check_gateway` tells a dead
stack (a non-JSON 502 from nginx) from an application 502 (a JSON envelope),
and health probes count any 502 as down. Both hold only while the page keeps
the 502 status and an HTML body, so these guards pin that, and pin that git
tracks the file at all (its folder ignores `*.conf`).

`docker/host-nginx-restart-page.conf` gives the servers' host nginx the same
page. It is held to the same status and body rules, its body must equal the
container's so the two cannot drift, and it must not turn on
`proxy_intercept_errors` itself: that would swallow the app's JSON 502s. The
operator adds that line only to the SEEK locations (`DEPLOYMENT.md` section 4).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONF = REPO_ROOT / "docker" / "nginx-optional" / "error-pages.conf"
HOST_CONF = REPO_ROOT / "docker" / "host-nginx-restart-page.conf"
BOTH = pytest.mark.parametrize("conf", [CONF, HOST_CONF], ids=["container", "host"])
# The container's nginx makes a 504 only after an hour-long request, so it keeps 502 alone. The
# host nginx gives up on a wedged web after its read timeout, so it also pages a 504 it makes.
ERROR_PAGE = {CONF: "502 @nextseek_restarting", HOST_CONF: "502 504 @nextseek_restarting"}


def _directives(conf: Path = CONF) -> str:
    return "\n".join(l for l in conf.read_text().splitlines() if not l.lstrip().startswith("#"))


def _body(conf: Path) -> str:
    return re.search(r"return 502 '(.*)';", _directives(conf), re.S).group(1)


@BOTH
def test_error_page_keeps_the_502_status(conf):
    text = _directives(conf)
    pages = re.findall(r"^\s*error_page\s+([^;]+);", text, re.M)
    assert pages == [ERROR_PAGE[conf]], pages
    assert "=" not in pages[0], "error_page must not rewrite the status"
    assert re.search(r"return 502 '", text), "the named location must answer 502"


@BOTH
def test_error_page_body_is_html_and_safe_for_nginx(conf):
    assert "default_type text/html;" in _directives(conf)
    body = _body(conf)
    assert body.startswith("<!doctype html>")
    assert "$" not in body, "nginx would expand a dollar sign as a variable"
    assert "'" not in body, "a single quote would end nginx's string"


def test_error_page_is_tracked_despite_the_folder_ignoring_conf_files():
    lines = [l.strip() for l in (CONF.parent / ".gitignore").read_text().splitlines()]
    assert "*.conf" in lines
    assert "!error-pages.conf" in lines[lines.index("*.conf"):], "the negation must follow *.conf"


def test_host_page_body_is_the_container_page_body():
    assert _body(HOST_CONF) == _body(CONF)


def test_host_snippet_never_intercepts_upstream_errors_itself():
    # Included server-wide, an intercept here would reach the app (:8000) locations.
    text = _directives(HOST_CONF)
    assert "proxy_intercept_errors" not in text
    assert "proxy_pass" not in text


def test_the_app_nginx_includes_the_drop_in_folder_inside_its_server_block():
    nginx_conf = _directives(REPO_ROOT / "docker" / "nginx.conf")
    assert "include /etc/nginx/optional/*.conf;" in nginx_conf[nginx_conf.index("server {"):]
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "./docker/nginx-optional:/etc/nginx/optional:ro" in compose
