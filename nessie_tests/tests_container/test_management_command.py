"""In-container wiring test for `manage.py nessie` (no live turns)."""
import pytest
from django.core.management import call_command

from nessie_tests import runner
from nessie_tests.manifest import NessieManifest, NessieManifestEntry


def test_nessie_command_wires_run_suite(monkeypatch):
    captured = {}

    def fake_run_suite(**kw):
        captured.update(kw)
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"], entries=[])

    monkeypatch.setattr(runner, "run_suite", fake_run_suite)
    call_command("nessie", "--tier", "full", "--scope", "all", "--out", "/tmp/nessie_out_test")

    assert captured["tier"] == "full"
    assert captured["scope"] == "all"
    assert captured["run_consistency"] is True          # auto-on for --tier full
    assert captured["auth_header"].startswith("Basic ")
    assert captured["bundle_reader"] is not None         # full tier wires the bundle reader


def test_nessie_command_route_tier_no_consistency_no_bundle(monkeypatch):
    captured = {}
    monkeypatch.setattr(runner, "run_suite",
                        lambda **kw: (captured.update(kw)
                                      or NessieManifest(started_at="a", ended_at="b",
                                                        tier=kw["tier"], scope=kw["scope"], entries=[])))
    call_command("nessie", "--tier", "route", "--out", "/tmp/nessie_out_test")
    assert captured["run_consistency"] is False
    assert captured["bundle_reader"] is None


def test_nessie_command_exits_nonzero_on_real_failure(monkeypatch):
    def fake_run_suite(**kw):
        return NessieManifest(
            started_at="a", ended_at="b", tier="route", scope="specific",
            entries=[NessieManifestEntry(id="x", family="f", tier="route",
                                         status="failed", expected_fail=False)])

    monkeypatch.setattr(runner, "run_suite", fake_run_suite)
    with pytest.raises(SystemExit) as ei:
        call_command("nessie", "--tier", "route", "--out", "/tmp/nessie_out_test")
    assert ei.value.code == 1
