import importlib


def test_telemetry_env_var_does_not_break_the_benchmark_settings(monkeypatch):
    for p in ("ATTRIBUTE_DEFAULT_DB", "ATTRIBUTE_TEST_DB"):
        for k, v in (("HOST", "h"), ("PORT", "3306"), ("USER", "u"), ("PASSWORD", "x")):
            monkeypatch.setenv(f"{p}_{k}", v)
    monkeypatch.setenv("ATTRIBUTE_DEFAULT_DATABASE_NAME", "a")
    monkeypatch.setenv("ATTRIBUTE_TEST_DATABASE_NAME", "b")
    monkeypatch.setenv("ATTRIBUTE_WORKER_TELEMETRY_RESULTS", "1")
    mod = importlib.import_module("dmac.attribute_performance_settings")
    importlib.reload(mod)
    assert mod.SEEK_DATABASE == "seek"
