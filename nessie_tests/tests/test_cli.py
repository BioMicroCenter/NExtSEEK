from nessie_tests import cli


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
