from pathlib import Path

import pytest

from nessie_tests import bayes_manifest, bayesian, cli, preflight


def test_parser_defaults():
    a = cli.build_parser().parse_args(["--base-url", "http://h:8000"])
    assert a.tier == "route" and a.scope == "specific" and a.base_url == "http://h:8000"
    assert a.consistency is False


def _capture(monkeypatch):
    captured = {}
    def fake_run_suite(**kw):
        captured.update(kw)
        from nessie_tests.manifest import NessieManifest
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"], entries=[])
    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    return captured


def test_main_wires_run_suite(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    rc = cli.main(["--base-url", "http://h:8000", "--tier", "full", "--scope", "all",
                   "--user", "demo", "--password", "demopassword", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["tier"] == "full" and captured["scope"] == "all"
    assert captured["auth_header"].startswith("Basic ")
    # --tier full auto-runs the #33 consistency groups
    assert captured["run_consistency"] is True


def test_full_tier_auto_enables_consistency(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    cli.main(["--base-url", "http://h:8000", "--tier", "full", "--out", str(tmp_path)])
    assert captured["run_consistency"] is True


def test_route_tier_defaults_consistency_off(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    cli.main(["--base-url", "http://h:8000", "--tier", "route", "--out", str(tmp_path)])
    assert captured["run_consistency"] is False


def test_consistency_flag_forces_it_on_route_tier(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    cli.main(["--base-url", "http://h:8000", "--tier", "route", "--consistency", "--out", str(tmp_path)])
    assert captured["run_consistency"] is True


def test_the_cli_reports_an_outage_rather_than_silently_dropping_it(monkeypatch, tmp_path, capsys):
    """`0 real failures` on a run that lost 10 cases to Bedrock would be a lie."""
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    def fake_run_suite(**kw):
        return NessieManifest(
            started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"],
            entries=[NessieManifestEntry(id="sys.q", family="f", tier="full",
                                         status="error", outage=True)])

    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    rc = cli.main(["--base-url", "http://h:8000", "--out", str(tmp_path)])

    assert rc == 0, "an outage must not fail the gate"
    assert "provider outage" in capsys.readouterr().out


def test_the_cli_names_a_case_that_asserted_nothing(monkeypatch, tmp_path, capsys):
    """`1 real failure` with no explanation reads as a regression. It is not one."""
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    def fake_run_suite(**kw):
        return NessieManifest(
            started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"],
            entries=[NessieManifestEntry(id="green.refine_recall", family="f", tier="full",
                                         status="no_assertions")])

    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    rc = cli.main(["--base-url", "http://h:8000", "--out", str(tmp_path)])

    assert rc == 1, "a case that asserted nothing must fail the gate"
    assert "asserted nothing" in capsys.readouterr().out


def _manifest_with(monkeypatch, *entries):
    from nessie_tests.manifest import NessieManifest

    def fake_run_suite(**kw):
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"],
                              scope=kw["scope"], entries=list(entries))

    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)


def test_the_cli_does_not_print_a_cost_it_never_observed(monkeypatch, tmp_path, capsys):
    """A route-tier run keeps billing after the poll loop breaks; $0 is a lie."""
    from nessie_tests.manifest import NessieManifestEntry
    _manifest_with(monkeypatch, NessieManifestEntry(id="gate.cc", family="nessie_route",
                                                    tier="route", status="passed"))

    cli.main(["--base-url", "http://h:8000", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert "unmeasured" in out
    assert "$0.0" not in out


def test_the_cli_still_prints_a_cost_it_did_observe(monkeypatch, tmp_path, capsys):
    """Non-vacuity: the summary must not go silent about money it can account for."""
    from nessie_tests.manifest import NessieManifestEntry
    _manifest_with(monkeypatch, NessieManifestEntry(id="cc.q", family="f", tier="full",
                                                    status="passed", cost=0.37))

    cli.main(["--base-url", "http://h:8000", "--tier", "full", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert "$0.37" in out
    assert "unmeasured" not in out


# --- --bayesian -------------------------------------------------------------


def _capture_paired(monkeypatch, *, result=None, raises=None):
    """Stand in for the paired orchestrator. Never touches the network."""
    captured = {}

    def fake_run_paired(**kw):
        captured.update(kw)
        if raises is not None:
            raise raises
        return result if result is not None else bayes_manifest.BayesManifest()

    monkeypatch.setattr(bayesian, "run_paired", fake_run_paired)
    return captured


def _tripwire_on_every_spend(monkeypatch):
    """Make any path that could bill a paid turn fail loudly instead.

    A ~260-arm paid run hangs off these three entry points. An argument-validation
    test that reached one of them would be buying turns from a real endpoint, so
    the tests below prove the CLI exits BEFORE any of them rather than assuming it.
    """
    def boom(*args, **kw):
        raise AssertionError("the CLI reached a spending path; it should have "
                             "exited during argument validation")

    monkeypatch.setattr(bayesian, "run_paired", boom)
    monkeypatch.setattr(cli.runner, "run_suite", boom)
    monkeypatch.setattr(cli.http_driver, "make_default_clients", boom)


def test_bayesian_flag_parses_with_its_own_options():
    a = cli.build_parser().parse_args(
        ["--base-url", "http://x", "--bayesian", "--max-usd", "40", "--resume"])
    assert a.bayesian is True and a.max_usd == 40.0 and a.resume is True


def test_bayesian_defaults_its_own_output_directory(monkeypatch):
    """Asserted on what `run_paired` RECEIVES, not on `parse_args`.

    `--out`'s default depends on `--bayesian`, which argparse cannot express, so
    it is resolved in `main` and the parsed value is None. Pinning the parsed
    value would pin None; pinning the passed value pins the directory the paid
    run actually writes into.
    """
    captured = _capture_paired(monkeypatch)
    assert cli.main(["--base-url", "http://x", "--bayesian"]) == 0
    assert captured["out_dir"] == Path("nessie_out_bayes")


def test_a_normal_run_still_defaults_to_nessie_out(monkeypatch):
    """`--out`'s default became None to make the line above possible. The
    existing, unrelated, already-shipped path must not notice."""
    captured = _capture(monkeypatch)
    assert cli.main(["--base-url", "http://x"]) == 0
    assert captured["out_dir"] == Path("nessie_out")


def test_bayesian_threads_its_whole_run_shape_through(monkeypatch, tmp_path):
    captured = _capture_paired(monkeypatch)
    cli.main(["--base-url", "http://x", "--bayesian", "--max-usd", "40", "--resume",
              "--full-timeout", "900", "--pace", "1.5", "--out", str(tmp_path)])
    assert captured["max_usd"] == 40.0 and captured["resume"] is True
    assert captured["full_timeout_s"] == 900.0 and captured["pace_s"] == 1.5
    assert captured["out_dir"] == tmp_path
    assert captured["base_url"] == "http://x"
    assert captured["auth_header"].startswith("Basic ")


@pytest.mark.parametrize("extra", [
    ["--tier", "full"], ["--scope", "all"], ["--sample", "0.5"], ["--seed", "3"],
    ["--family", "reporting"], ["--variant", "green.mus_ndma"],
])
def test_bayesian_refuses_every_other_selection_source(extra, monkeypatch, capsys):
    """is_bayesian IS the selection. Two sources for 'what ran' makes a run
    unexplainable, which is the same reason `run_suite`'s cases_path refuses them.

    `--family` and `--variant` are NOT in the plan's list and are live on the
    parser. `--scope` defaults to "specific", so `--bayesian --family reporting`
    cleared the entire check and was then silently ignored: exactly the
    second-selection-source hazard the check exists to prevent.
    """
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["--base-url", "http://x", "--bayesian", *extra])
    # Only the text AFTER argparse's "error:" prefix counts. The usage line above
    # it lists every option name on the parser, so asserting against the whole of
    # stderr would pass for any flag whether or not the check names it.
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and extra[0] in msg


@pytest.mark.parametrize("extra", [["--max-usd", "40"], ["--resume"],
                                   ["--full-timeout", "900"]])
def test_the_paired_only_flags_refuse_to_be_silently_ignored(extra, monkeypatch, capsys):
    """The mirror hazard. `run_suite` has no budget ceiling, no resume and no
    per-turn deadline parameter, so accepting these on a normal run would leave an
    operator believing a spending cap is in force on a paid full-tier run while
    nothing whatsoever is capped."""
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["--base-url", "http://x", "--tier", "full", *extra])
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and extra[0] in msg


def test_bayesian_points_at_the_paired_manifest_not_the_normal_one(monkeypatch, tmp_path, capsys):
    """`manifest.json` is what a NORMAL run writes and is the name the paired
    writer deliberately abandoned. Printing it would send the operator to a path
    that does not exist, named after the collision this branch already fixed."""
    from nessie_tests.manifest import NessieManifestEntry
    done = NessieManifestEntry(id="a.b", family="f", tier="full", status="passed")
    result = bayes_manifest.BayesManifest(pairs=[
        bayes_manifest.BayesPair(id="a.b", family="f", ns=done, cc=done),
        bayes_manifest.BayesPair(id="c.d", family="f", ns=done),
    ])
    _capture_paired(monkeypatch, result=result)

    assert cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "1/2 complete pairs" in out
    assert f"{tmp_path}/{bayes_manifest.MANIFEST_NAME}" in out
    assert "/manifest.json" not in out, "that is the NORMAL run's manifest name"


@pytest.mark.parametrize("exc, code, must_say", [
    (bayesian.BudgetExceeded("spent $40.00 of $40.00 before x.y:cc."), 2,
     ["spent $40.00", bayes_manifest.MANIFEST_NAME, "--resume"]),
    (bayesian.PriorRunWouldBeOverwritten("already holds 130 pair(s)"), 3,
     ["130 pair(s)", "nothing was billed"]),
    (preflight.ForceRouteRejected("force_route was not honoured"), 4,
     ["force_route was not honoured", "nothing was billed"]),
    (bayesian.CorpusChanged("prior fingerprint 'a', current 'b'"), 5,
     ["prior fingerprint 'a'", "nothing was billed"]),
])
def test_every_paired_abort_is_legible_from_the_terminal_alone(
        exc, code, must_say, monkeypatch, tmp_path, capsys):
    """Four aborts, four exit codes, and in every case the cause survives to the
    terminal. A traceback would tell an operator who just spent real money that
    something raised, not whether the run is resumable or whether it ran at all.

    The codes are distinct because a wrapper script must tell "money spent, work
    on disk, resumable" from "refused before spending anything" without parsing
    English out of stdout.
    """
    _capture_paired(monkeypatch, raises=exc)

    rc = cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)])

    assert rc == code
    out = capsys.readouterr().out
    for phrase in must_say:
        assert phrase in out, f"{phrase!r} missing from:\n{out}"
