"""Unit tests for the readiness gate's credential decision. No stack, no network.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      --with playwright pytest ci/smoke/test_readiness_unit.py -q -p no:cacheprovider

`startup rebuild` always passes --wait-ready and reports its own "CI passed" from
the suite's exit code. So the one thing the gate must never do is go quiet on a
box with no credentials: every test would skip, pytest would exit 0, and a deploy
nothing had verified would be reported as verified. That refusal is asserted here
rather than discovered on the box.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from ci.smoke.conftest import resolve_readiness_credentials

# pytest.exit raises this. Bound once so the tests read as what they assert.
Exit = pytest.exit.Exception


class FakeConfig:
    """The whole of the pytest config surface the decision touches."""

    def __init__(self, wait_ready=False):
        self._options = {"--wait-ready": wait_ready}

    def getoption(self, name):
        return self._options[name]


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch, tmp_path):
    """No credentials anywhere: not in the environment, not in a file.

    NEXTSEEK_CI_ENV is pointed at a path that does not exist, so the real
    ~/.config/nextseek/ci.env on the developer's own box cannot make these tests
    pass for the wrong reason.
    """
    for var in ("CI_SMOKE_USER", "CI_SMOKE_PASS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "no-such-ci.env"))


def test_no_gate_requested_means_no_credentials_needed():
    """Local iteration without credentials still degrades to the per-test skips."""
    assert resolve_readiness_credentials(FakeConfig(wait_ready=False)) is None


def test_gate_without_credentials_exits_2():
    """The bug this prevents: --wait-ready with no ci.env used to skip every
    test, exit 0, and let `startup rebuild` print "CI passed" having run nothing."""
    with pytest.raises(Exit) as e:
        resolve_readiness_credentials(FakeConfig(wait_ready=True))
    assert e.value.returncode == 2
    assert "CI_SMOKE_USER/CI_SMOKE_PASS" in str(e.value)
    assert "~/.config/nextseek/ci.env" in str(e.value)
    assert "cannot do its job" in str(e.value)


def test_gate_with_environment_credentials_returns_them(monkeypatch):
    monkeypatch.setenv("CI_SMOKE_USER", "smoke")
    monkeypatch.setenv("CI_SMOKE_PASS", "pw")
    assert resolve_readiness_credentials(FakeConfig(wait_ready=True)) == ("smoke", "pw")


def test_gate_with_file_credentials_returns_them(monkeypatch, tmp_path):
    """The credential file is the documented source; the env only overrides it."""
    cred = tmp_path / "ci.env"
    cred.write_text('CI_SMOKE_USER="fromfile"\nCI_SMOKE_PASS=filepw\n')
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(cred))
    assert resolve_readiness_credentials(FakeConfig(wait_ready=True)) == (
        "fromfile", "filepw")


def test_half_a_credential_is_no_credential(monkeypatch):
    """A user with no password cannot authenticate, so it must not pass the gate."""
    monkeypatch.setenv("CI_SMOKE_USER", "smoke")
    with pytest.raises(Exit) as e:
        resolve_readiness_credentials(FakeConfig(wait_ready=True))
    assert e.value.returncode == 2
