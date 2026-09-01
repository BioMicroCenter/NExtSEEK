"""Unit tests for the profile resolver. No stack, no network, no browser.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      --with playwright pytest ci/smoke/test_profile_unit.py -q -p no:cacheprovider

resolve_profile decides, before anything else runs, whether this process is
allowed to write. Every refusal it can make is asserted here rather than
discovered on the box that would have been written to.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from ci.routes import PROFILES
from ci.smoke.conftest import _PROFILE_RANK, _redact_console, resolve_profile

# pytest.exit raises this. Bound once so the tests read as what they assert.
Exit = pytest.exit.Exception


class FakeConfig:
    """The whole of the pytest config surface resolve_profile touches.

    A real Config cannot be built without starting a session, and the point of
    these tests is that the decision is reachable without one.
    """

    def __init__(self, profile=None, force_profile=None):
        self._options = {"--profile": profile, "--force-profile": force_profile}

    def getoption(self, name):
        return self._options[name]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Neither variable is set unless a test sets it."""
    monkeypatch.delenv("CI_BOX_PROFILE", raising=False)
    monkeypatch.delenv("CI_FORCE_PROFILE_CONFIRM", raising=False)


# --------------------------------------------------------------------------- #
# the rank table
# --------------------------------------------------------------------------- #

def test_every_registry_profile_has_a_rank():
    """The bug this prevents: a fourth profile passes _valid_profile and then
    raises a bare KeyError inside the widening comparison."""
    assert set(_PROFILE_RANK) == set(PROFILES)
    assert len(set(_PROFILE_RANK.values())) == len(PROFILES), "two profiles rank equal"


def test_prod_is_the_narrowest_rank():
    """The direction the comparison depends on: a wider profile ranks higher."""
    assert _PROFILE_RANK["prod"] == 0
    assert _PROFILE_RANK["local"] == max(_PROFILE_RANK.values())
    assert _PROFILE_RANK["prod"] < _PROFILE_RANK["dev"] < _PROFILE_RANK["local"]


# --------------------------------------------------------------------------- #
# the box's declaration
# --------------------------------------------------------------------------- #

def test_absent_declaration_means_prod():
    """Fail closed. An unconfigured box is treated as the one that must not be
    written to, not as the one that may."""
    assert resolve_profile(FakeConfig()) == "prod"


@pytest.mark.parametrize("declared", PROFILES)
def test_declaration_is_honoured_when_nothing_is_asked_for(monkeypatch, declared):
    monkeypatch.setenv("CI_BOX_PROFILE", declared)
    assert resolve_profile(FakeConfig()) == declared


def test_unknown_declaration_exits_2_naming_the_allowed_list(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "staging")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig())
    assert e.value.returncode == 2
    assert "'staging' is not a profile" in str(e.value)
    assert "local, dev, prod" in str(e.value)


# --------------------------------------------------------------------------- #
# --profile: narrowing only
# --------------------------------------------------------------------------- #

def test_narrowing_is_allowed(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "local")
    assert resolve_profile(FakeConfig(profile="prod")) == "prod"
    assert resolve_profile(FakeConfig(profile="dev")) == "dev"


def test_asking_for_the_declared_profile_is_allowed(monkeypatch):
    """Equal rank is not a widening. The comparison is >, not >=."""
    monkeypatch.setenv("CI_BOX_PROFILE", "dev")
    assert resolve_profile(FakeConfig(profile="dev")) == "dev"


def test_widening_exits_2(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(profile="local"))
    assert e.value.returncode == 2
    assert "would widen past the box's 'prod'" in str(e.value)


def test_unknown_asked_profile_exits_2_naming_the_allowed_list(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "local")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(profile="pord"))
    assert e.value.returncode == 2
    assert "--profile 'pord' is not a profile" in str(e.value)
    assert "local, dev, prod" in str(e.value)


# --------------------------------------------------------------------------- #
# --force-profile: two keys, not one
# --------------------------------------------------------------------------- #

def test_force_without_the_confirmation_exits_2(monkeypatch):
    """The flag alone is not enough, so a line copied out of a workflow file
    cannot widen anything on its own."""
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(force_profile="local"))
    assert e.value.returncode == 2
    assert "CI_FORCE_PROFILE_CONFIRM=yes" in str(e.value)
    assert "The box declares 'prod'" in str(e.value)


def test_force_with_a_wrong_confirmation_exits_2(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    monkeypatch.setenv("CI_FORCE_PROFILE_CONFIRM", "1")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(force_profile="local"))
    assert e.value.returncode == 2


def test_force_with_the_confirmation_widens_and_announces_itself(monkeypatch, capsys):
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    monkeypatch.setenv("CI_FORCE_PROFILE_CONFIRM", "yes")
    assert resolve_profile(FakeConfig(force_profile="local")) == "local"
    assert "*** FORCED PROFILE 'local' on a box declaring 'prod' ***" in capsys.readouterr().out


def test_unknown_forced_profile_exits_2_naming_the_allowed_list(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    monkeypatch.setenv("CI_FORCE_PROFILE_CONFIRM", "yes")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(force_profile="locl"))
    assert e.value.returncode == 2
    assert "--force-profile 'locl' is not a profile" in str(e.value)
    assert "local, dev, prod" in str(e.value)


def test_both_options_at_once_exits_2(monkeypatch):
    """Refused rather than given a precedence rule, which nobody would read."""
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    monkeypatch.setenv("CI_FORCE_PROFILE_CONFIRM", "yes")
    with pytest.raises(Exit) as e:
        resolve_profile(FakeConfig(profile="dev", force_profile="local"))
    assert e.value.returncode == 2
    assert "were both given" in str(e.value)


# --------------------------------------------------------------------------- #
# memoisation
# --------------------------------------------------------------------------- #

def test_the_answer_is_memoised_on_the_config(monkeypatch):
    """One decision per run. Two callers must not be able to disagree, and the
    environment changing mid-run must not move the profile underneath them."""
    monkeypatch.setenv("CI_BOX_PROFILE", "local")
    config = FakeConfig()
    assert resolve_profile(config) == "local"
    assert config._nextseek_profile == "local"

    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    assert resolve_profile(config) == "local"


def test_memoisation_does_not_leak_between_configs(monkeypatch):
    monkeypatch.setenv("CI_BOX_PROFILE", "local")
    assert resolve_profile(FakeConfig()) == "local"
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")
    assert resolve_profile(FakeConfig()) == "prod"


def test_a_cached_answer_is_returned_without_consulting_anything(monkeypatch):
    """Task 6 calls this from a collection hook after pytest_configure already
    has. The second call must not re-print the banner or re-read the options."""
    monkeypatch.setenv("CI_BOX_PROFILE", "prod")

    class Explodes(FakeConfig):
        def getoption(self, name):
            raise AssertionError("options were re-read after the answer was cached")

    config = Explodes()
    config._nextseek_profile = "dev"
    assert resolve_profile(config) == "dev"


# --------------------------------------------------------------------------- #
# console redaction (the other thing the resolved profile decides)
# --------------------------------------------------------------------------- #

# What a failed XHR looks like in the console once the discovery fixture has put a
# real identifier into the URL the page asked for.
CONSOLE_LINE = (
    "console.error: Failed to load resource: the server responded with a status "
    "of 502 (Bad Gateway) http://box/nextseek_api/samples/A.TIS-240101XYZ-7/"
)


def test_a_console_line_is_redacted_under_prod():
    out = _redact_console(CONSOLE_LINE, "prod")
    assert "A.TIS-240101XYZ-7" not in out, f"an identifier survived redaction: {out}"
    assert "<url>" in out
    # The half that says what went wrong is untouched.
    assert "502 (Bad Gateway)" in out


def test_every_url_on_the_line_is_redacted_not_only_the_first():
    line = "pageerror: http://box/a/1/ failed while loading https://box/b/2/"
    out = _redact_console(line, "prod")
    assert out.count("<url>") == 2
    assert "http" not in out


@pytest.mark.parametrize("profile", ["local", "dev"])
def test_a_console_line_is_left_alone_off_prod(profile):
    """On a box whose data is not production data the URL is the useful half."""
    assert _redact_console(CONSOLE_LINE, profile) == CONSOLE_LINE
