"""Post-seed apply of SEEK's DB-backed `site_base_host` setting.

SEEK stamps this value into the "SEEK ID" it displays, its JSON-LD @id
identifiers, its sitemap, and it validates pasted SEEK IDs against it. It has no
env-var mechanism in fairdom/seek:1.15.1 -- a `settings` row is the only lever --
and the committed seed carries no such row, so a fresh install silently runs on
SEEK's shipped default (http://localhost:3000).

This step runs after the seed load and BEFORE SEEK's first boot, so the
entrypoint's boot-time sitemap build already sees the right value and no restart
is ever required.

Semantics are deliberately set-if-absent: an existing row is an operator/admin
decision (production's is hand-set) and must never be clobbered by tooling.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.steps.seek_settings import (
    SITE_BASE_HOST_VAR,
    apply_site_base_host,
    encode_setting_value,
)


class TestEncoding:
    def test_encodes_as_seek_yaml_scalar(self) -> None:
        """SEEK stores settings YAML-encoded; the 93 seeded rows use '--- <v>\\n'."""
        assert encode_setting_value("https://fairdata.mit.edu") == "--- https://fairdata.mit.edu\n"


@patch("startup.steps.seek_settings.compose_exec")
class TestApply:
    def _env(self) -> dict[str, str]:
        return {"MYSQL_ROOT_PASSWORD": "rootpw"}

    def test_absent_row_is_inserted(self, mock_exec: MagicMock) -> None:
        # table exists -> "1"; existing value lookup -> "" (no row)
        mock_exec.side_effect = ["1\n", "\n", ""]
        status = apply_site_base_host(
            Path("/repo"), self._env(), "https://fairdata-dev.mit.edu"
        )
        assert status == "applied"
        sql = " ".join(str(c) for c in mock_exec.call_args_list)
        assert "INSERT INTO" in sql and SITE_BASE_HOST_VAR in sql
        assert "fairdata-dev.mit.edu" in sql

    def test_existing_equal_value_is_a_noop(self, mock_exec: MagicMock) -> None:
        mock_exec.side_effect = ["1\n", "--- https://fairdata-dev.mit.edu\n"]
        status = apply_site_base_host(
            Path("/repo"), self._env(), "https://fairdata-dev.mit.edu"
        )
        assert status == "already set"
        sql = " ".join(str(c) for c in mock_exec.call_args_list)
        assert "INSERT INTO" not in sql
        assert "UPDATE" not in sql

    def test_existing_different_value_is_never_clobbered(self, mock_exec: MagicMock) -> None:
        """PROD SAFETY: an admin-set row wins over anything the tooling wants.

        Production's site_base_host was set by hand. A tooling write here would
        silently repoint every identifier prod emits.
        """
        mock_exec.side_effect = ["1\n", "--- https://fairdata.mit.edu\n"]
        status = apply_site_base_host(
            Path("/repo"), self._env(), "https://fairdata-dev.mit.edu"
        )
        assert status.startswith("differs")
        sql = " ".join(str(c) for c in mock_exec.call_args_list)
        assert "INSERT INTO" not in sql, "must not insert over an existing row"
        assert "UPDATE" not in sql, "must not update an admin-set row"
        # the warning has to name both values or it is not actionable
        assert "https://fairdata.mit.edu" in status
        assert "https://fairdata-dev.mit.edu" in status

    def test_missing_settings_table_is_skipped(self, mock_exec: MagicMock) -> None:
        """--no-seed / fresh volume: nothing to configure yet, don't explode."""
        mock_exec.side_effect = ["0\n"]
        status = apply_site_base_host(
            Path("/repo"), self._env(), "https://fairdata-dev.mit.edu"
        )
        assert status == "settings table missing"

    def test_idempotent_second_run_does_not_write(self, mock_exec: MagicMock) -> None:
        mock_exec.side_effect = ["1\n", "\n", ""]
        assert apply_site_base_host(Path("/repo"), self._env(), "https://x.example.org") == "applied"
        mock_exec.reset_mock()
        mock_exec.side_effect = ["1\n", "--- https://x.example.org\n"]
        assert apply_site_base_host(Path("/repo"), self._env(), "https://x.example.org") == "already set"
        sql = " ".join(str(c) for c in mock_exec.call_args_list)
        assert "INSERT INTO" not in sql

    def test_targets_the_global_scope_row(self, mock_exec: MagicMock) -> None:
        """SEEK scopes settings by (target_type,target_id); the global row is NULL/NULL."""
        mock_exec.side_effect = ["1\n", "\n", ""]
        apply_site_base_host(Path("/repo"), self._env(), "https://x.example.org")
        sql = " ".join(str(c) for c in mock_exec.call_args_list)
        assert "target_type IS NULL" in sql
        assert "target_id IS NULL" in sql

    def test_runs_against_the_db_service(self, mock_exec: MagicMock) -> None:
        mock_exec.side_effect = ["1\n", "\n", ""]
        apply_site_base_host(Path("/repo"), self._env(), "https://x.example.org")
        assert all(c.kwargs.get("service") == "db" for c in mock_exec.call_args_list)
