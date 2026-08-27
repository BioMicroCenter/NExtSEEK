from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.lib.deploy_source import DeploySourceError, resolve_verified_source


def _git_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


def _verified_dispatch(runtime: Path, source: Path, *, source_dirty: str = ""):
    def dispatch(command, **_kwargs):
        repo = Path(command[2])
        args = tuple(command[3:])
        if args == ("rev-parse", "HEAD"):
            return _git_result("abc123\n")
        if repo == source and args == ("rev-parse", "origin/dev"):
            return _git_result("abc123\n")
        if repo == source and args == ("status", "--porcelain", "--untracked-files=all"):
            return _git_result(source_dirty)
        if repo == runtime and args[:3] == ("status", "--porcelain", "--"):
            return _git_result("")
        raise AssertionError((repo, args))

    return dispatch


def test_separate_clean_source_preserves_runtime_root(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    runtime.mkdir()
    source.mkdir()

    with patch(
        "startup.lib.deploy_source.subprocess.run",
        side_effect=_verified_dispatch(runtime, source),
    ):
        assert resolve_verified_source(runtime, source) == source.resolve()


def test_separate_source_must_be_clean(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    runtime.mkdir()
    source.mkdir()

    with patch(
        "startup.lib.deploy_source.subprocess.run",
        side_effect=_verified_dispatch(runtime, source, source_dirty=" M docker-compose.yml\n"),
    ), pytest.raises(DeploySourceError, match="deploy source is dirty"):
        resolve_verified_source(runtime, source)


def test_separate_source_must_equal_origin_dev(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    runtime.mkdir()
    source.mkdir()

    def dispatch(command, **_kwargs):
        args = tuple(command[3:])
        if args == ("rev-parse", "HEAD"):
            return _git_result("feature\n")
        if args == ("rev-parse", "origin/dev"):
            return _git_result("dev\n")
        raise AssertionError(args)

    with patch("startup.lib.deploy_source.subprocess.run", side_effect=dispatch), pytest.raises(
        DeploySourceError, match="is not origin/dev"
    ):
        resolve_verified_source(runtime, source)


def test_runtime_deploy_controls_must_remain_committed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    runtime.mkdir()
    source.mkdir()
    base_dispatch = _verified_dispatch(runtime, source)

    def dispatch(command, **kwargs):
        args = tuple(command[3:])
        if Path(command[2]) == runtime and args[:3] == ("status", "--porcelain", "--"):
            return _git_result(" M startup/cli.py\n")
        return base_dispatch(command, **kwargs)

    with patch("startup.lib.deploy_source.subprocess.run", side_effect=dispatch), pytest.raises(
        DeploySourceError, match="runtime deployment controls are dirty"
    ):
        resolve_verified_source(runtime, source)
