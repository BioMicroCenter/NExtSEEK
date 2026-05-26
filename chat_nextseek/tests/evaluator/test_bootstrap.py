"""Tests for the BAML client bootstrap (DD-40..DD-42, R-H1)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_nextseek.evaluator import bootstrap as bootstrap_mod


@pytest.fixture
def fake_baml_src(tmp_path: Path) -> Path:
    src = tmp_path / "baml_src"
    src.mkdir()
    (src / "evaluator.baml").write_text("function Dummy() {}", encoding="utf-8")
    return src


@pytest.fixture
def fake_client_dir(tmp_path: Path) -> Path:
    return tmp_path / "baml_client"


def _patch_paths(monkeypatch, baml_src: Path, client_dir: Path) -> None:
    monkeypatch.setattr(bootstrap_mod, "_resolve_baml_src", lambda: baml_src)
    monkeypatch.setattr(bootstrap_mod, "_resolve_client_dir", lambda: client_dir)


def test_bootstrap_regenerates_when_client_absent(monkeypatch, fake_baml_src, fake_client_dir):
    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        fake_client_dir.mkdir(parents=True, exist_ok=True)
        (fake_client_dir / "inlinedbaml.py").write_text("# generated", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap_mod.subprocess, "run", fake_run)

    bootstrap_mod._bootstrap_baml_client()

    assert len(calls) == 1
    assert calls[0][:3] == ["uv", "run", "baml-cli"]
    assert str(fake_baml_src) in calls[0]


def test_bootstrap_regenerates_when_stale(monkeypatch, fake_baml_src, fake_client_dir, tmp_path):
    fake_client_dir.mkdir()
    inlined = fake_client_dir / "inlinedbaml.py"
    inlined.write_text("# generated", encoding="utf-8")
    # Deterministic mtimes: client older than source.
    os.utime(inlined, (1_000, 1_000))
    os.utime(fake_baml_src / "evaluator.baml", (2_000, 2_000))

    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap_mod.subprocess, "run", fake_run)

    bootstrap_mod._bootstrap_baml_client()
    assert len(calls) == 1


def test_bootstrap_skips_when_fresh(monkeypatch, fake_baml_src, fake_client_dir):
    fake_client_dir.mkdir()
    inlined = fake_client_dir / "inlinedbaml.py"
    inlined.write_text("# generated", encoding="utf-8")
    # Deterministic mtimes: client newer than source.
    os.utime(fake_baml_src / "evaluator.baml", (1_000, 1_000))
    os.utime(inlined, (2_000, 2_000))

    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap_mod.subprocess,
        "run",
        lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0),
    )

    bootstrap_mod._bootstrap_baml_client()
    assert calls == []


def test_bootstrap_respects_env_escape_hatch(monkeypatch, fake_baml_src, fake_client_dir):
    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)
    monkeypatch.setenv("CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP", "1")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap_mod.subprocess,
        "run",
        lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0),
    )

    bootstrap_mod._bootstrap_baml_client()
    assert calls == []


def test_bootstrap_raises_when_uv_missing(monkeypatch, fake_baml_src, fake_client_dir):
    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("uv not on PATH")

    monkeypatch.setattr(bootstrap_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        bootstrap_mod._bootstrap_baml_client()
    assert "uv run baml-cli generate" in str(exc.value)
    assert "operations.md#baml-regeneration" in str(exc.value)


def test_bootstrap_raises_on_nonzero_exit(monkeypatch, fake_baml_src, fake_client_dir):
    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr=b"boom")

    monkeypatch.setattr(bootstrap_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        bootstrap_mod._bootstrap_baml_client()
    assert "baml-cli generate failed" in str(exc.value)
    assert "boom" in str(exc.value)


def test_resolve_baml_src_from_package_resources():
    path = bootstrap_mod._resolve_baml_src()
    assert path.is_dir()
    assert any(p.suffix == ".baml" for p in path.iterdir())


def test_resolve_client_dir_points_at_src_tree():
    path = bootstrap_mod._resolve_client_dir()
    # src layout: .../src/baml_client
    assert path.name == "baml_client"
    assert path.parent.name == "src"


def test_resolve_baml_src_fallback_on_non_path(monkeypatch):
    # importlib.resources can return a MultiplexedPath in wheel installs.
    # Confirm the fallback `Path(__file__).parent / "baml_src"` path is taken.
    import importlib.resources as _resources

    class _Fake:
        def __truediv__(self, other):
            return object()  # not a Path

    monkeypatch.setattr(_resources, "files", lambda pkg: _Fake())
    path = bootstrap_mod._resolve_baml_src()
    assert path == Path(bootstrap_mod.__file__).parent / "baml_src"


def test_resolve_baml_src_fallback_on_module_not_found(monkeypatch):
    import importlib.resources as _resources

    def _raise(_pkg):
        raise ModuleNotFoundError("nope")

    monkeypatch.setattr(_resources, "files", _raise)
    path = bootstrap_mod._resolve_baml_src()
    assert path == Path(bootstrap_mod.__file__).parent / "baml_src"


def test_bootstrap_decodes_stderr_none(monkeypatch, fake_baml_src, fake_client_dir):
    # CalledProcessError with stderr=None must not crash the decode branch.
    _patch_paths(monkeypatch, fake_baml_src, fake_client_dir)

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr=None)

    monkeypatch.setattr(bootstrap_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        bootstrap_mod._bootstrap_baml_client()
    assert "baml-cli generate failed" in str(exc.value)


def test_main_module_invokes_bootstrap(monkeypatch):
    # __main__.py uses attribute-form `bootstrap._bootstrap_baml_client()`
    # so this monkeypatch on the module is visible to the runpy-run __main__.
    calls: list[bool] = []
    monkeypatch.setattr(bootstrap_mod, "_bootstrap_baml_client", lambda: calls.append(True))
    # Simulate runner returning 0 without actually running the CLI.
    import chat_nextseek.evaluator.runner as runner_mod

    monkeypatch.setattr(runner_mod, "main", lambda: 0)

    with pytest.raises(SystemExit) as exc:
        import runpy

        runpy.run_module("chat_nextseek.evaluator", run_name="__main__")
    assert exc.value.code == 0
    assert calls == [True]
