"""`manage.py nessie` flags for the graph_search Nessie POC: forced runs and arms.

`call_command` with `run_suite` and `run_arms` patched, so no turn is ever sent.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from NessieAI import paths
from NessieAI.tests.nessie_tests import http_driver, preflight, runner
from NessieAI.tests.nessie_tests.manifest import NessieManifest, NessieManifestEntry

SECRET = "not-a-real-password-7f3a"
PW_ENV = "GS_TEST_NESSIE_PASSWORD"


def _manifest(tier="full", scope="s", entries=()):
    return NessieManifest(started_at="a", ended_at="b", tier=tier, scope=scope,
                          entries=list(entries))


@pytest.fixture
def suite(monkeypatch):
    captured = {}

    def fake_run_suite(**kw):
        captured.update(kw)
        return _manifest(tier=kw["tier"], scope=kw["scope"])

    monkeypatch.setattr(runner, "run_suite", fake_run_suite)
    return captured


@pytest.fixture
def arms(monkeypatch):
    captured = {}

    def fake_run_arms(**kw):
        captured.update(kw)
        entries = [
            NessieManifestEntry(id="q.one", family="f", tier="full", status="passed"),
            NessieManifestEntry(id="q.two", family="f", tier="full", status="failed",
                                failed_criteria=["t0:last_reply"]),
            NessieManifestEntry(id="q.three", family="f", tier="full", status="error",
                                outage=True),
        ]
        return {"run_meta": {}, "questions": [],
                "progress": {"state": "complete", "turns_driven": 6},
                "manifests": {a: _manifest(scope=f"arm:{a}", entries=entries)
                              for a in kw["arms"]},
                "arms_file": str(Path(kw["out_dir"]) / "arms.json")}

    monkeypatch.setattr(runner, "run_arms", fake_run_arms)
    return captured


@pytest.fixture
def no_suite(monkeypatch):
    def boom(**kw):
        raise AssertionError("run_suite must not run")
    monkeypatch.setattr(runner, "run_suite", boom)


def _call(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command("nessie", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _arms_args(tmp_path, *extra):
    return ["--tier", "full", "--cases", str(tmp_path / "cases.json"),
            "--force-route", "ns", "--out", str(tmp_path / "run"), *extra]


# ── --arms ───────────────────────────────────────────────────────────────────


def test_arms_reach_run_arms(arms, no_suite, tmp_path):
    _call(*_arms_args(tmp_path, "--arms", "graph,api"))
    assert arms["arms"] == ["graph", "api"]
    assert arms["cases_path"] == str(tmp_path / "cases.json")
    assert arms["out_dir"] == tmp_path / "run"
    assert arms["corpus_path"] == paths.NESSIE_CORPUS
    assert arms["resume"] is False and arms["max_turns"] is None
    assert arms["base_url"] == "http://localhost:8000"
    assert arms["auth_header"] == http_driver.basic_auth("demo", "demopassword")
    assert arms["bundle_reader"] is not None, "a full turn reads the bundle as run_suite does"


def test_one_arm_is_a_list_of_one(arms, no_suite, tmp_path):
    _call(*_arms_args(tmp_path, "--arms", "graph"))
    assert arms["arms"] == ["graph"]


def test_resume_and_max_turns_reach_run_arms(arms, tmp_path):
    _call(*_arms_args(tmp_path, "--arms", "graph,api", "--resume", "--max-turns", "60"))
    assert arms["resume"] is True and arms["max_turns"] == 60


def test_arms_print_each_arm_the_error_and_outage_counts_and_the_arms_file(arms, tmp_path):
    out, _ = _call(*_arms_args(tmp_path, "--arms", "graph,api"))
    lines = out.splitlines()
    for arm in ("graph", "api"):
        line = next(ln for ln in lines if ln.strip().startswith(f"arm {arm}"))
        assert "3 questions" in line
        assert "error 1" in line and "1 provider outage" in line
    assert str(tmp_path / "run" / "arms.json") in out
    assert "complete" in out


def test_a_wrong_answer_in_an_arm_is_the_measurement_not_a_gate_failure(arms, tmp_path):
    _call(*_arms_args(tmp_path, "--arms", "graph,api"))   # no SystemExit


@pytest.mark.parametrize("drop, why", [
    ("--cases", "--cases"),
    ("--force-route", "--force-route ns"),
])
def test_arms_need_cases_and_the_ns_route(drop, why, arms, no_suite, tmp_path):
    args = _arms_args(tmp_path, "--arms", "graph,api")
    i = args.index(drop)
    del args[i:i + 2]
    with pytest.raises(CommandError, match=why):
        _call(*args)
    assert arms == {}


def test_arms_refuse_the_cc_route(arms, tmp_path):
    args = _arms_args(tmp_path, "--arms", "graph")
    args[args.index("ns")] = "cc"
    with pytest.raises(CommandError, match="--force-route ns"):
        _call(*args)
    assert arms == {}


def test_arms_exclude_a_single_parser_force(arms, tmp_path):
    with pytest.raises(CommandError, match="drop --force-parser-mode"):
        _call(*_arms_args(tmp_path, "--arms", "graph,api", "--force-parser-mode", "graph"))
    assert arms == {}


def test_arms_drive_full_turns_only(arms, tmp_path):
    args = _arms_args(tmp_path, "--arms", "graph")
    args[args.index("full")] = "route"
    with pytest.raises(CommandError, match="--tier full"):
        _call(*args)
    assert arms == {}


@pytest.mark.parametrize("names", ["graph,cypher", "", "graph,graph"])
def test_a_bad_arm_list_is_refused(names, arms, tmp_path):
    with pytest.raises(CommandError, match="--arms takes distinct names"):
        _call(*_arms_args(tmp_path, "--arms", names))
    assert arms == {}


@pytest.mark.parametrize("extra", [["--resume"], ["--max-turns", "10"]])
def test_resume_and_max_turns_need_arms(extra, suite, tmp_path):
    with pytest.raises(CommandError, match="--arms"):
        _call("--tier", "full", "--out", str(tmp_path), *extra)
    assert suite == {}


@pytest.mark.parametrize("exc", [
    preflight.ParserForceRejected("no evaluation-switch note on the 'graph' probe"),
    preflight.ForceRouteRejected("force_route was not honoured"),
    runner.CasesChanged("the cases file changed"),
    runner.NoArmsRunToResume("nothing to resume"),
])
def test_a_refusal_becomes_a_command_error_carrying_its_message(exc, monkeypatch, tmp_path):
    def raising(**kw):
        raise exc
    monkeypatch.setattr(runner, "run_arms", raising)
    with pytest.raises(CommandError) as e:
        _call(*_arms_args(tmp_path, "--arms", "graph,api"))
    assert str(exc) in str(e.value)


def test_a_preflight_refusal_says_its_probe_turns_were_billed(monkeypatch, tmp_path):
    def raising(**kw):
        raise preflight.ParserForceRejected("no note")
    monkeypatch.setattr(runner, "run_arms", raising)
    with pytest.raises(CommandError) as e:
        _call(*_arms_args(tmp_path, "--arms", "graph,api"))
    assert "probe" in str(e.value) and "no question" in str(e.value)


# ── --password-env ───────────────────────────────────────────────────────────


def test_password_env_reads_the_variable_and_never_prints_it(suite, monkeypatch, tmp_path,
                                                              capsys):
    monkeypatch.setenv(PW_ENV, SECRET)
    out, err = _call("--tier", "route", "--password-env", PW_ENV, "--out", str(tmp_path))
    assert suite["auth_header"] == http_driver.basic_auth("demo", SECRET)
    printed = out + err + "".join(capsys.readouterr())
    assert SECRET not in printed


def test_password_env_reaches_run_arms(arms, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(PW_ENV, SECRET)
    out, err = _call(*_arms_args(tmp_path, "--arms", "graph", "--password-env", PW_ENV))
    assert arms["auth_header"] == http_driver.basic_auth("demo", SECRET)
    assert SECRET not in out + err + "".join(capsys.readouterr())


def test_a_missing_password_variable_is_named_without_a_value(suite, monkeypatch, tmp_path):
    monkeypatch.delenv(PW_ENV, raising=False)
    with pytest.raises(CommandError, match=f"--password-env {PW_ENV}: that environment variable"):
        _call("--tier", "route", "--password-env", PW_ENV, "--out", str(tmp_path))
    assert suite == {}


def test_password_and_password_env_are_exclusive(suite, monkeypatch, tmp_path):
    monkeypatch.setenv(PW_ENV, SECRET)
    with pytest.raises(CommandError, match="not both") as e:
        _call("--tier", "route", "--password", "x", "--password-env", PW_ENV,
              "--out", str(tmp_path))
    assert SECRET not in str(e.value)
    assert suite == {}


def test_the_default_password_is_unchanged(suite, tmp_path):
    _call("--tier", "route", "--out", str(tmp_path))
    assert suite["auth_header"] == http_driver.basic_auth("demo", "demopassword")


# ── forcing a normal run ─────────────────────────────────────────────────────


def test_a_normal_run_forces_nothing_by_default(suite, tmp_path):
    _call("--tier", "route", "--out", str(tmp_path))
    assert suite["force_route"] is None and suite["force_parser_mode"] is None


def test_the_force_flags_reach_run_suite(suite, tmp_path):
    _call("--tier", "full", "--force-route", "ns", "--force-parser-mode", "api",
          "--out", str(tmp_path))
    assert suite["force_route"] == "ns" and suite["force_parser_mode"] == "api"


def test_force_route_alone_reaches_run_suite(suite, tmp_path):
    _call("--tier", "full", "--force-route", "cc", "--out", str(tmp_path))
    assert suite["force_route"] == "cc" and suite["force_parser_mode"] is None


@pytest.mark.parametrize("extra", [[], ["--force-route", "cc"]])
def test_a_parser_force_needs_the_ns_route(extra, suite, tmp_path):
    with pytest.raises(CommandError, match="--force-route ns"):
        _call("--tier", "full", "--force-parser-mode", "graph", *extra, "--out", str(tmp_path))
    assert suite == {}
