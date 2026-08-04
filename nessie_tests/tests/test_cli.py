import urllib.error
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


def test_bayesian_names_the_same_corpus_the_normal_path_names(monkeypatch):
    """`_CORPUS` exists so the CLI names its corpus instead of inheriting whatever
    default the callee happens to hold. Letting the paired path fall through to
    `run_paired`'s own default made the two run shapes resolve the corpus by
    different rules, and the paired run's fingerprint is precisely what makes
    `--resume` refuse to continue onto a changed corpus."""
    captured = _capture_paired(monkeypatch)
    cli.main(["--base-url", "http://x", "--bayesian"])
    assert captured["corpus_path"] == cli._CORPUS


@pytest.mark.parametrize("extra", [
    ["--tier", "full"], ["--scope", "all"], ["--sample", "0.5"], ["--seed", "3"],
    ["--family", "reporting"], ["--variant", "green.mus_ndma"], ["--consistency"],
])
def test_bayesian_refuses_every_other_selection_source(extra, monkeypatch, capsys):
    """is_bayesian IS the selection. Two sources for 'what ran' makes a run
    unexplainable, which is the same reason `run_suite`'s cases_path refuses them.

    `--family` and `--variant` are NOT in the plan's list and are live on the
    parser. `--scope` defaults to "specific", so `--bayesian --family reporting`
    cleared the entire check and was then silently ignored: exactly the
    second-selection-source hazard the check exists to prevent. `--consistency`
    is here for the same reason: `run_paired` has no consistency-group parameter
    at all, so it was accepted and dropped on the floor.
    """
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--bayesian", *extra])
    assert e.value.code == 2, "argparse.error() owns 2; see the abort-code test"
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
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--tier", "full", *extra])
    assert e.value.code == 2, "argparse.error() owns 2; see the abort-code test"
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and extra[0] in msg


# --- supplied, not merely different from the default -------------------------


@pytest.mark.parametrize("extra", [
    ["--tier", "route"], ["--scope", "specific"], ["--sample", "1.0"],
    ["--seed", "0"], ["--consistency"],
])
def test_bayesian_refuses_a_conflicting_flag_set_to_its_default_value(
        extra, monkeypatch, capsys):
    """The exclusion asks the wrong question if it compares VALUES.

    `--bayesian --tier route` was accepted and reached `run_paired`, returning 0:
    a ~322-arm full-depth paid run for an operator who had explicitly asked for
    the cheap route tier, because "route" happens to be `--tier`'s default. What
    matters is whether the flag was SUPPLIED, and design §7.6 says `--bayesian`
    refuses to combine with `--tier` -- not with some of its values.
    """
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--bayesian", *extra])
    assert e.value.code == 2
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and extra[0] in msg


def test_a_normal_run_refuses_a_paired_only_flag_set_to_its_default_value(
        monkeypatch, capsys):
    """The same correction on the mirror check. `--full-timeout 600` is the
    default value, so a value comparison saw nothing and accepted a flag that
    `run_suite` has no parameter for and silently ignores."""
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--tier", "full",
                  "--full-timeout", str(cli.FULL_TIMEOUT_DEFAULT_S)])
    assert e.value.code == 2
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and "--full-timeout" in msg


def test_supplied_flags_reports_only_what_was_typed():
    """The primitive both checks now rest on, asserted directly: it must not be
    fooled by a default-valued flag, and must not invent one that was absent."""
    assert cli._supplied_flags(["--base-url", "http://x"]) == set()
    assert cli._supplied_flags(["--base-url", "http://x", "--tier", "route"]) == {"tier"}
    # `--tier=full` and abbreviations are argparse's rules, not ours; delegating
    # to a second parse is why they hold rather than being re-implemented.
    assert cli._supplied_flags(["--base-url", "http://x", "--tier=full"]) == {"tier"}
    assert cli._supplied_flags(["--base-url", "http://x", "--resume"]) == {"resume"}


def test_neither_check_disturbs_a_run_that_supplied_nothing_extra(monkeypatch, tmp_path):
    """Non-vacuity for both corrections: the ordinary invocations must still run."""
    captured = _capture(monkeypatch)
    assert cli.main(["--base-url", "http://x", "--out", str(tmp_path)]) == 0
    assert captured["tier"] == "route"

    paired = _capture_paired(monkeypatch)
    assert cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)]) == 0
    assert paired["out_dir"] == tmp_path


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
    (bayesian.BudgetExceeded("spent $40.00 of $40.00 before x.y:cc."), 3,
     ["spent $40.00", bayes_manifest.MANIFEST_NAME, "--resume"]),
    (bayesian.PriorRunWouldBeOverwritten("already holds 130 pair(s)"), 4,
     ["130 pair(s)", "nothing was billed"]),
    (preflight.ForceRouteRejected("force_route was not honoured"), 5,
     ["force_route was not honoured", "no paired arm was billed"]),
    (bayesian.CorpusChanged("prior fingerprint 'a', current 'b'"), 6,
     ["prior fingerprint 'a'", "nothing was billed"]),
    (urllib.error.URLError("[Errno 111] Connection refused"), 7,
     ["http://x", "Connection refused"]),
    (bayesian.NoRunToResume("--resume was given but ... holds no paired manifest"), 8,
     ["holds no paired manifest", "nothing was billed"]),
])
def test_every_paired_abort_is_legible_from_the_terminal_alone(
        exc, code, must_say, monkeypatch, tmp_path, capsys):
    """Six aborts, six exit codes, and in every case the cause survives to the
    terminal. A traceback would tell an operator who just spent real money that
    something raised, not whether the run is resumable or whether it ran at all.

    The codes are distinct because a wrapper script must tell "money spent, work
    on disk, resumable" from "refused before spending anything" without parsing
    English out of stdout. None of them may be 0, 1 or 2: those are already taken
    by success, a normal run's real failures, and argparse's own usage error.
    A wrapper that raised --max-usd and retried on "the budget code" would loop
    forever on a mistyped flag if the budget code were 2.

    URLError is here because it is the likeliest first-run failure of all, a wrong
    port. It is raised by the preflight's own POST, so no harness guard ever sees
    it, and it reached the operator as fifteen lines of urllib frames under exit
    1 -- the code that means "a normal run had real failures".
    """
    _capture_paired(monkeypatch, raises=exc)

    rc = cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)])

    assert rc == code
    assert rc not in (0, 1, 2), "0/1/2 already mean success, gate failure, bad args"
    out = capsys.readouterr().out
    for phrase in must_say:
        assert phrase in out, f"{phrase!r} missing from:\n{out}"


def test_the_preflight_abort_does_not_claim_a_zero_bill(monkeypatch, tmp_path, capsys):
    """Exit 5 fires AFTER the preflight drove a real forced-NS turn, and a turn
    keeps billing after the harness stops polling it -- which is exactly why the
    normal run's cost line says `unmeasured` rather than $0.00. "$0" here is the
    one claim `manifest.cost_summary` refuses to make. The other refusals fire
    before any turn is sent and may keep saying it."""
    _capture_paired(monkeypatch, raises=preflight.ForceRouteRejected("dropped"))
    assert cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)]) == 5
    out = capsys.readouterr().out
    assert "nothing was billed" not in out
    assert "probe turn" in out, "the one turn that WAS billed is not named"


@pytest.mark.parametrize("exc", [
    bayesian.PriorRunWouldBeOverwritten("prior run"),
    bayesian.NoRunToResume("no prior run"),
    bayesian.CorpusChanged("drift"),
])
def test_the_refusals_that_really_are_free_still_say_so(exc, monkeypatch, tmp_path, capsys):
    """Non-vacuity for the line above: these three fire before the preflight, so
    they genuinely cost nothing and must go on saying it."""
    _capture_paired(monkeypatch, raises=exc)
    cli.main(["--base-url", "http://x", "--bayesian", "--out", str(tmp_path)])
    assert "nothing was billed" in capsys.readouterr().out
