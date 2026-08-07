"""DJANGO_DEBUG must only enable debug for explicit truthy values.

dmac/settings.py used to read ``DEBUG = os.getenv("DJANGO_DEBUG", False)``,
which turns ANY non-empty string — including "False" — into debug-on
(NExtSTEPS.md historically instructed setting DJANGO_DEBUG=False as a
hardening step). These tests exec the real DEBUG assignment line from
settings.py under controlled environments so the guard cannot drift from
the shipped code, and never import the settings module itself.
"""

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS = REPO_ROOT / "dmac" / "settings.py"


def _debug_line() -> str:
    for line in SETTINGS.read_text().splitlines():
        if line.startswith("DEBUG = "):
            return line
    raise AssertionError("no `DEBUG = ` assignment found in dmac/settings.py")


def _eval_debug(env_value):
    namespace = {"os": os}
    old = os.environ.pop("DJANGO_DEBUG", None)
    try:
        if env_value is not None:
            os.environ["DJANGO_DEBUG"] = env_value
        exec(_debug_line(), namespace)
    finally:
        if old is None:
            os.environ.pop("DJANGO_DEBUG", None)
        else:
            os.environ["DJANGO_DEBUG"] = old
    return namespace["DEBUG"]


@pytest.mark.parametrize(
    "value", [None, "", "False", "false", "FALSE", "0", "no", "off", "   "]
)
def test_debug_stays_off(value):
    assert _eval_debug(value) is False


@pytest.mark.parametrize(
    "value", ["1", "true", "True", "TRUE", "yes", "YES", " true "]
)
def test_debug_turns_on(value):
    assert _eval_debug(value) is True


def test_footgun_pattern_gone():
    text = SETTINGS.read_text()
    assert 'os.getenv("DJANGO_DEBUG", False)' not in text
    assert re.search(r"^DEBUG = ", text, flags=re.M), "DEBUG assignment missing"
