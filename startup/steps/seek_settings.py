"""Post-seed apply of SEEK's DB-backed `site_base_host` setting.

Why this exists
---------------
SEEK derives its own public identity from `site_base_host`: the "SEEK ID" shown
on every show page, the JSON-LD `@id` identifiers, the sitemap built at boot, and
the host it validates pasted SEEK IDs against. `fairdom/seek:1.15.1` exposes NO
environment variable for it (its docker/entrypoint.sh handles only
RAILS_RELATIVE_URL_ROOT / OPENBIS_USERNAME / NO_ENTRYPOINT_WORKERS / DB+Solr
vars), so a row in the `settings` table is the only lever. The committed seed
carries 93 settings rows but none for `site_base_host`, so a fresh install runs
on SEEK's shipped default `http://localhost:3000` and emits unreachable
identifiers.

Ordering
--------
Invoked next to schema_fixups.apply_all -- after the seed load, before SEEK's
first boot. At that point no Rails process has read (and cached) the config, and
the entrypoint's boot-time sitemap build sees the correct value immediately. That
is why no restart is ever needed.

Semantics: set-if-absent, never clobber
---------------------------------------
An existing row is an operator decision (production's is admin-set). Tooling that
overwrote it would silently repoint every identifier that instance publishes, so
a differing row is reported loudly and left alone.
"""
from __future__ import annotations

from pathlib import Path

from startup.lib.docker_ops import DockerOpsError, compose_exec

SEEK_DATABASE = "seek_production"
SETTINGS_TABLE = "settings"
SITE_BASE_HOST_VAR = "site_base_host"


def _root_password(env: dict[str, str]) -> str:
    return env.get("MYSQL_ROOT_PASSWORD", "seek_root")


def encode_setting_value(url: str) -> str:
    """Encode a scalar the way SEEK's RailsSettings rows store it.

    Verified against the 93 rows in the committed seed: a YAML document with a
    plain scalar, e.g. ``--- https://seek.example.org\\n``.
    """
    return f"--- {url}\n"


def _sql_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _table_exists(repo_root: Path, env: dict[str, str]) -> bool:
    out = compose_exec(
        service="db",
        command=[
            "mysql", "-uroot", f"-p{_root_password(env)}", "-N", "-e",
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA='{SEEK_DATABASE}' AND TABLE_NAME='{SETTINGS_TABLE}';",
        ],
        project_dir=repo_root,
        env=env,
    )
    return out.strip().splitlines()[0].strip() == "1"


def _current_value(repo_root: Path, env: dict[str, str]) -> str | None:
    """Return the stored global-scope value, or None when no row exists.

    SEEK scopes settings by (target_type, target_id); the instance-wide row is
    the NULL/NULL one.
    """
    out = compose_exec(
        service="db",
        command=[
            "mysql", "-uroot", f"-p{_root_password(env)}", "-N", "-e",
            f"SELECT value FROM {SEEK_DATABASE}.{SETTINGS_TABLE} "
            f"WHERE var='{SITE_BASE_HOST_VAR}' "
            "AND target_type IS NULL AND target_id IS NULL;",
        ],
        project_dir=repo_root,
        env=env,
    )
    raw = (out or "").strip()
    return raw or None


def _decode_setting_value(raw: str) -> str:
    """Pull the scalar back out of SEEK's YAML encoding for comparison."""
    text = raw.strip()
    if text.startswith("---"):
        text = text[3:]
    return text.strip()


def _insert(repo_root: Path, env: dict[str, str], url: str) -> None:
    # Explicit INSERT rather than ON DUPLICATE KEY: the unique index over
    # (target_type, target_id, var) has NULL components, and MySQL permits
    # multiple NULLs in a unique index, so an upsert would not dedupe reliably.
    encoded = _sql_quote(encode_setting_value(url))
    compose_exec(
        service="db",
        command=[
            "mysql", "-uroot", f"-p{_root_password(env)}", SEEK_DATABASE, "-e",
            f"INSERT INTO {SETTINGS_TABLE} (var, value, target_type, target_id, created_at, updated_at) "
            f"VALUES ('{SITE_BASE_HOST_VAR}', '{encoded}', NULL, NULL, NOW(), NOW());",
        ],
        project_dir=repo_root,
        env=env,
    )


def apply_site_base_host(repo_root: Path, env: dict[str, str], url: str) -> str:
    """Ensure SEEK's site_base_host is set, without ever clobbering an existing row.

    Returns a human-readable status:
      "settings table missing" - nothing seeded yet (e.g. --no-seed); skipped
      "already set"            - row present and equal; no write
      "differs: ..."           - row present and different; NOT overwritten
      "applied"                - row absent; inserted
    """
    try:
        if not _table_exists(repo_root, env):
            return "settings table missing"

        current_raw = _current_value(repo_root, env)
        if current_raw is not None:
            current = _decode_setting_value(current_raw)
            if current == url:
                return "already set"
            return (
                f"differs: SEEK already has {SITE_BASE_HOST_VAR}={current!r} "
                f"(configured: {url!r}); left unchanged. An existing value is an "
                "admin decision -- change it in SEEK's admin UI (Server admin -> "
                "Settings -> Site base Hostname) or via "
                f"`rails runner 'Seek::Config.{SITE_BASE_HOST_VAR} = \"{url}\"'`."
            )

        _insert(repo_root, env, url)
        return "applied"
    except DockerOpsError as exc:  # DB unreachable / mysql non-zero: report, don't abort
        return f"skipped ({exc.__class__.__name__})"
