"""Resolution of the per-instance browser-reachable SEEK base URL.

SEEK_PUBLIC_URL is the one value that must be correct per instance:
  dev    -> https://fairdata-dev.mit.edu
  prod   -> https://fairdata.mit.edu
  laptop -> http://localhost:<seek_port>

It had no home in the pipeline, so operators hand-edited the rendered
docker/nextseek.env -- which `install` then silently overwrote from a template
whose default is only correct on a laptop. These tests pin the precedence that
makes a re-run of `install` non-destructive.
"""

from pathlib import Path

import pytest

from startup.steps.config import (
    DEFAULT_SEEK_PUBLIC_URL_HOST,
    InvalidSeekPublicUrl,
    read_rendered_seek_public_url,
    resolve_seek_public_url,
)


def _repo_with_env(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "docker" / "nextseek.env").write_text(body)
    return repo


def _empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    return repo


class TestPrecedence:
    def test_explicit_flag_wins(self, tmp_path: Path) -> None:
        repo = _repo_with_env(tmp_path, 'SEEK_PUBLIC_URL="https://stale.example.org"\n')
        got = resolve_seek_public_url(
            repo, explicit="https://fairdata-dev.mit.edu", instance_value="https://old.example.org", seek_port=3000
        )
        assert got == "https://fairdata-dev.mit.edu"

    def test_hand_set_env_value_is_preserved_over_default(self, tmp_path: Path) -> None:
        """PROD SAFETY: a re-run of `install` must not clobber a hand-set value.

        This is the exact regression that would have broken production: the
        template renders http://localhost:<port>, and render_nextseek_env
        overwrites unconditionally. Resolution must read the live file back.
        """
        repo = _repo_with_env(
            tmp_path,
            'SEEK_HOST="seek"\nSEEK_PUBLIC_URL="https://fairdata.mit.edu"\nOTHER="x"\n',
        )
        got = resolve_seek_public_url(repo, explicit=None, instance_value=None, seek_port=3000)
        assert got == "https://fairdata.mit.edu", "hand-set prod value must survive a re-render"
        assert "localhost" not in got

    def test_env_value_wins_over_instance_state(self, tmp_path: Path) -> None:
        repo = _repo_with_env(tmp_path, 'SEEK_PUBLIC_URL="https://fairdata.mit.edu"\n')
        got = resolve_seek_public_url(
            repo, explicit=None, instance_value="https://drifted.example.org", seek_port=3000
        )
        assert got == "https://fairdata.mit.edu"

    def test_instance_state_used_when_no_env_file(self, tmp_path: Path) -> None:
        repo = _empty_repo(tmp_path)
        got = resolve_seek_public_url(
            repo, explicit=None, instance_value="https://fairdata-dev.mit.edu", seek_port=3000
        )
        assert got == "https://fairdata-dev.mit.edu"

    def test_defaults_to_localhost_on_seek_port(self, tmp_path: Path) -> None:
        repo = _empty_repo(tmp_path)
        got = resolve_seek_public_url(repo, explicit=None, instance_value=None, seek_port=3042)
        assert got == f"{DEFAULT_SEEK_PUBLIC_URL_HOST}:3042"
        assert got == "http://localhost:3042"

    def test_default_tracks_port_offset_installs(self, tmp_path: Path) -> None:
        """SEEK's own default is hardcoded to :3000; ours must follow the port."""
        repo = _empty_repo(tmp_path)
        assert resolve_seek_public_url(repo, explicit=None, instance_value=None, seek_port=3100) == (
            "http://localhost:3100"
        )


class TestReadRendered:
    def test_reads_quoted_value(self, tmp_path: Path) -> None:
        repo = _repo_with_env(tmp_path, 'A="1"\nSEEK_PUBLIC_URL="https://fairdata.mit.edu"\nB="2"\n')
        assert read_rendered_seek_public_url(repo) == "https://fairdata.mit.edu"

    def test_reads_unquoted_value(self, tmp_path: Path) -> None:
        repo = _repo_with_env(tmp_path, "SEEK_PUBLIC_URL=https://fairdata.mit.edu\n")
        assert read_rendered_seek_public_url(repo) == "https://fairdata.mit.edu"

    def test_none_when_absent(self, tmp_path: Path) -> None:
        repo = _repo_with_env(tmp_path, 'SEEK_HOST="seek"\n')
        assert read_rendered_seek_public_url(repo) is None

    def test_none_when_no_file(self, tmp_path: Path) -> None:
        assert read_rendered_seek_public_url(_empty_repo(tmp_path)) is None

    def test_ignores_an_uninterpolated_template_placeholder(self, tmp_path: Path) -> None:
        """A half-rendered file must not poison resolution with a literal $VAR."""
        repo = _repo_with_env(tmp_path, 'SEEK_PUBLIC_URL="http://localhost:${SEEK_PORT}"\n')
        assert read_rendered_seek_public_url(repo) is None


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "https://fairdata.mit.edu/",       # trailing slash == path
            "https://fairdata.mit.edu/seek",   # path
            "fairdata.mit.edu",                # no scheme
            "ftp://fairdata.mit.edu",          # wrong scheme
            "https://",                        # no host
            "",                                # empty
            "http://seek:3000 ",               # stray whitespace
        ],
    )
    def test_rejects_malformed(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(InvalidSeekPublicUrl):
            resolve_seek_public_url(_empty_repo(tmp_path), explicit=bad, instance_value=None, seek_port=3000)

    @pytest.mark.parametrize(
        "good",
        [
            "https://fairdata-dev.mit.edu",
            "https://fairdata.mit.edu",
            "http://localhost:3000",
            "http://127.0.0.1:3042",
        ],
    )
    def test_accepts_wellformed(self, tmp_path: Path, good: str) -> None:
        assert resolve_seek_public_url(
            _empty_repo(tmp_path), explicit=good, instance_value=None, seek_port=3000
        ) == good

    def test_malformed_value_in_env_file_does_not_crash_resolution(self, tmp_path: Path) -> None:
        """A corrupt rendered file falls back rather than aborting the install."""
        repo = _repo_with_env(tmp_path, 'SEEK_PUBLIC_URL="not a url"\n')
        got = resolve_seek_public_url(repo, explicit=None, instance_value=None, seek_port=3000)
        assert got == "http://localhost:3000"
