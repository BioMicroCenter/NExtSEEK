"""Drift detection between the two SEEK-URL layers.

The original user-visible bug existed because these two drifted apart:
  Layer A  docker/nextseek.env SEEK_PUBLIC_URL  - how NExtSEEK links TO SEEK
  Layer B  SEEK's site_base_host settings row   - how SEEK identifies ITSELF

install() now renders both from one stored value, but they can still diverge out
of band: an admin edits SEEK's setting in its UI, or someone hand-edits the env.
doctor should say so rather than leave it to be discovered in a browser.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.lib.instance import InstanceState
from startup.steps.validate import check_seek_url_consistency


def _repo(tmp_path: Path, env_body: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    if env_body is not None:
        (repo / "docker" / "nextseek.env").write_text(env_body)
    return repo


def _state(url: str) -> InstanceState:
    return InstanceState(
        name="dev",
        prefix="",
        ports={"nextseek": 8000, "seek": 3000},
        compose_project_name="nextseek",
        created="2026-07-16T00:00:00Z",
        seek_public_url=url,
    )


@patch("startup.steps.validate.seek_settings.read_site_base_host")
class TestConsistency:
    def test_all_three_agree_is_ok(self, mock_read: MagicMock, tmp_path: Path) -> None:
        mock_read.return_value = "https://fairdata-dev.mit.edu"
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu"\n')
        r = check_seek_url_consistency(repo, _state("https://fairdata-dev.mit.edu"), {})
        assert r.ok is True
        assert r.warn is False

    def test_env_drifted_from_seek_is_flagged(self, mock_read: MagicMock, tmp_path: Path) -> None:
        """The exact original bug: NExtSEEK links one place, SEEK IDs another."""
        mock_read.return_value = "http://localhost:3000"
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu"\n')
        r = check_seek_url_consistency(repo, _state("https://fairdata-dev.mit.edu"), {})
        assert r.ok is False
        # both values must be named or the report is not actionable
        assert "https://fairdata-dev.mit.edu" in r.detail
        assert "http://localhost:3000" in r.detail

    def test_env_hand_edited_away_from_instance_state_is_flagged(
        self, mock_read: MagicMock, tmp_path: Path
    ) -> None:
        mock_read.return_value = "https://fairdata-dev.mit.edu"
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="https://hand-edited.example.org"\n')
        r = check_seek_url_consistency(repo, _state("https://fairdata-dev.mit.edu"), {})
        assert r.ok is False
        assert "hand-edited.example.org" in r.detail

    def test_seek_row_absent_is_flagged(self, mock_read: MagicMock, tmp_path: Path) -> None:
        """No row means SEEK is silently on its localhost:3000 default."""
        mock_read.return_value = None
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu"\n')
        r = check_seek_url_consistency(repo, _state("https://fairdata-dev.mit.edu"), {})
        assert r.ok is False
        assert "not set" in r.detail.lower() or "default" in r.detail.lower()

    def test_seek_unreachable_warns_rather_than_fails(
        self, mock_read: MagicMock, tmp_path: Path
    ) -> None:
        """doctor runs against stopped stacks too; that is not a config defect."""
        mock_read.side_effect = RuntimeError("db down")
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu"\n')
        r = check_seek_url_consistency(repo, _state("https://fairdata-dev.mit.edu"), {})
        assert r.warn is True

    def test_localhost_default_on_a_named_instance_warns(
        self, mock_read: MagicMock, tmp_path: Path
    ) -> None:
        """Consistent-but-localhost on a real deployment is worth surfacing."""
        mock_read.return_value = "http://localhost:3000"
        repo = _repo(tmp_path, 'SEEK_PUBLIC_URL="http://localhost:3000"\n')
        r = check_seek_url_consistency(repo, _state("http://localhost:3000"), {})
        assert r.ok is True  # internally consistent, so not a failure
