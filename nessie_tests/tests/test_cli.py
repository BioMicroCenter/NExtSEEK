from nessie_tests import cli


def test_parser_defaults():
    a = cli.build_parser().parse_args(["--base-url", "http://h:8000"])
    assert a.tier == "route" and a.scope == "specific" and a.base_url == "http://h:8000"


def test_main_wires_run_suite(monkeypatch, tmp_path):
    captured = {}
    def fake_run_suite(**kw):
        captured.update(kw)
        from nessie_tests.manifest import NessieManifest
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"], entries=[])
    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    rc = cli.main(["--base-url", "http://h:8000", "--tier", "full", "--scope", "all",
                   "--user", "demo", "--password", "demopassword", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["tier"] == "full" and captured["scope"] == "all"
    assert captured["auth_header"].startswith("Basic ")
