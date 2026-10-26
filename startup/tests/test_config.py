"""Tests for startup.steps.config."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from startup.lib.env import parse_line, read_env
from startup.lib.instance import InstanceState, save_instance
from startup.steps.config import (
    ConfigValues,
    UnsafeKeptValue,
    default_values,
    render_db_env,
    ensure_seek_nginx_conf,
    render_nextseek_env,
    render_local_settings,
    render_root_env,
    csrf_origins_for_port,
    kept_values,
    preview_changed_keys,
    read_rendered_value,
)


# The published defaults, read from the module rather than restated here.
# The short historical defaults install no longer falls back to.
_OLD = ("seek_root", "seek_db_password", "demopassword")


def test_default_values_generates_database_passwords() -> None:
    a = default_values(nextseek_port=8000, seek_port=3000)
    b = default_values(8000, 3000)
    for name in ("mysql_root_password", "mysql_password", "neo4j_password"):
        va, vb = getattr(a, name), getattr(b, name)
        assert va not in _OLD
        assert len(va) >= 24 and va.isalnum()
        assert va != vb
    assert a.django_secret_key  # auto-generated, non-empty


def test_kept_passwords_win_over_generation() -> None:
    v = default_values(8000, 3000, kept={"MYSQL_ROOT_PASSWORD": "x" * 30})
    assert v.mysql_root_password == "x" * 30


def test_existing_volumes_keep_the_kept_passwords() -> None:
    kept = {"MYSQL_ROOT_PASSWORD": "r" * 8, "MYSQL_PASSWORD": "m" * 8, "NEXTSEEK_NEO4J_PASSWORD": "n" * 8}
    v = default_values(8000, 3000, kept=kept, generate_passwords=False)
    assert (v.mysql_root_password, v.mysql_password, v.neo4j_password) == ("r" * 8, "m" * 8, "n" * 8)


def test_preview_does_not_flag_generated_passwords(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    flagged = preview_changed_keys(repo)["kept_differs_from_template"]
    for key in ("MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "NEXTSEEK_NEO4J_PASSWORD"):
        assert key not in flagged


def test_default_values_csrf_origins_match_port() -> None:
    v = default_values(nextseek_port=8042, seek_port=3042)
    assert "127.0.0.1:8042" in v.django_csrf_trusted_origins
    assert "localhost:8042" in v.django_csrf_trusted_origins


def test_csrf_origins_for_port_returns_both_hosts() -> None:
    result = csrf_origins_for_port(8001)
    assert "http://127.0.0.1:8001" in result
    assert "http://localhost:8001" in result


def test_render_db_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    template_path = repo / "startup" / "templates" / "db.env.template"
    template_path.write_text('MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}"\nMYSQL_PASSWORD="${MYSQL_PASSWORD}"\n')

    v = ConfigValues(
        mysql_root_password="root_pw",
        mysql_password="user_pw",
        neo4j_password="np",
        django_secret_key="dsk",
        django_csrf_trusted_origins="origins",
        nextseek_port=8000,
        seek_port=3000,
    )
    render_db_env(repo, v)
    rendered = (repo / "docker" / "db.env").read_text()
    assert 'MYSQL_ROOT_PASSWORD="root_pw"' in rendered
    assert 'MYSQL_PASSWORD="user_pw"' in rendered


def test_render_nextseek_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    template_path = repo / "startup" / "templates" / "nextseek.env.template"
    template_path.write_text(
        'NEXTSEEK_HOSTNAME="127.0.0.1:${NEXTSEEK_PORT}"\n'
        'NEXTSEEK_NEO4J_PASSWORD="${NEO4J_PASSWORD}"\n'
        'DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}"\n'
        'DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS}"\n'
        'SEEK_PUBLIC_URL="http://localhost:${SEEK_PORT}"\n'
    )
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="csrforigins",
        nextseek_port=8042, seek_port=3042,
    )
    render_nextseek_env(repo, v)
    rendered = (repo / "docker" / "nextseek.env").read_text()
    assert 'NEXTSEEK_HOSTNAME="127.0.0.1:8042"' in rendered
    assert 'NEO4J_PASSWORD="np"' in rendered
    assert 'DJANGO_SECRET_KEY="dsk"' in rendered
    assert 'DJANGO_CSRF_TRUSTED_ORIGINS="csrforigins"' in rendered
    assert 'SEEK_PUBLIC_URL="http://localhost:3042"' in rendered


def test_render_local_settings_writes_to_dmac(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "dmac").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    (repo / "startup" / "templates" / "local_settings.py.template").write_text("# template content\n")
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="o", nextseek_port=8000,
        seek_port=3000,
    )
    render_local_settings(repo, v)
    assert (repo / "dmac" / "local_settings.py").exists()
    assert "# template content" in (repo / "dmac" / "local_settings.py").read_text()


# --- Bug B (2026-07-07): chat_nextseek's REST self-calls run INSIDE the
# nextseek container, which listens on :8000 regardless of the published host
# port (gunicorn.conf.py, entrypoint.sh daphne branch). NEXTSEEK_BASE_URL keeps
# its public meaning (derives from NEXTSEEK_HOSTNAME = host-published port);
# the NEW NEXTSEEK_INTERNAL_BASE_URL carries the container-internal transport
# URL, decoupled from NEXTSEEK_PORT. Deriving the self-call URL from the host
# port broke every REST self-call on a port-bumped install (Step 7d greenfield).

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_real_template(
    tmp_path: Path, port: int, seek_port: int = 3000, seek_public_url: str | None = None
) -> str:
    """Render the REAL nextseek.env.template into a tmp repo skeleton."""
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "startup" / "templates").mkdir(parents=True)
    real_template = _REPO_ROOT / "startup" / "templates" / "nextseek.env.template"
    (repo / "startup" / "templates" / "nextseek.env.template").write_text(
        real_template.read_text()
    )
    render_nextseek_env(
        repo,
        default_values(nextseek_port=port, seek_port=seek_port, seek_public_url=seek_public_url),
    )
    return (repo / "docker" / "nextseek.env").read_text()


def test_real_template_renders_the_semantic_tuning_keys_commented(tmp_path: Path) -> None:
    """#28: the SEMANTIC_* tuning keys ship commented at the code defaults, with nothing left to fill."""
    lines = _render_real_template(tmp_path, port=8000).splitlines()
    semantic = [line for line in lines if "SEMANTIC_" in line]
    assert '# SEMANTIC_MODEL_NAME="sentence-transformers/all-MiniLM-L6-v2"' in semantic
    assert '# SEMANTIC_MAX_K="80"' in semantic
    assert not [line for line in semantic if "$" in line]
    assert not [line for line in semantic if line.startswith("SEMANTIC_")], "commented: the code defaults apply"


def test_real_template_renders_configured_seek_public_url(tmp_path: Path) -> None:
    """The public SEEK URL comes from instance config, not a hardcoded default."""
    rendered = _render_real_template(
        tmp_path, port=8042, seek_port=3042, seek_public_url="https://fairdata-dev.mit.edu"
    )
    assert 'SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu"' in rendered
    assert "localhost:3042" not in rendered.split("SEEK_PUBLIC_URL=")[1].splitlines()[0]


def test_real_template_seek_public_url_defaults_to_published_seek_port(tmp_path: Path) -> None:
    """With nothing configured, a laptop install still gets a working value."""
    rendered = _render_real_template(tmp_path, port=8042, seek_port=3042)
    assert 'SEEK_PUBLIC_URL="http://localhost:3042"' in rendered


def test_real_template_renders_public_and_internal_urls(tmp_path: Path) -> None:
    rendered = _render_real_template(tmp_path, port=8042)
    # Public hostname tracks the published host port ...
    assert 'NEXTSEEK_HOSTNAME="127.0.0.1:8042"' in rendered
    # ... the public base URL still derives from it (compose interpolation) ...
    assert 'NEXTSEEK_BASE_URL="http://$NEXTSEEK_HOSTNAME"' in rendered
    # ... and the internal transport URL is pinned to the in-container listener.
    assert 'NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"' in rendered


def test_real_template_internal_url_never_inherits_bumped_port(tmp_path: Path) -> None:
    rendered = _render_real_template(tmp_path, port=8001)
    internal_lines = [
        line for line in rendered.splitlines() if line.startswith("NEXTSEEK_INTERNAL_BASE_URL=")
    ]
    assert internal_lines == ['NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"']


def test_real_template_sets_the_cc_turn_ceiling_to_180(tmp_path: Path) -> None:
    """13b.2: the Container-CC turn ceiling is rendered, not left to the code
    default, so a box that renders its env is never silently unset. 180 is the
    operator's decision and matches the default in NessieAI/cc/cc_engine.py."""
    rendered = _render_real_template(tmp_path, port=8042)
    lines = [
        line for line in rendered.splitlines()
        if line.startswith("NEXTSEEK_CC_TIMEOUT_HARD_MAX=")
    ]
    assert lines == ['NEXTSEEK_CC_TIMEOUT_HARD_MAX="180"']


def test_env_example_internal_url_parity_with_template() -> None:
    """Tripwire: the hand-maintained example must carry the same internal URL."""
    example = (_REPO_ROOT / "docker" / "nextseek.env.example").read_text()
    lines = [
        line for line in example.splitlines() if line.startswith("NEXTSEEK_INTERNAL_BASE_URL=")
    ]
    assert lines == ['NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"']
    # The public base URL keeps its NEXTSEEK_HOSTNAME derivation.
    assert 'NEXTSEEK_BASE_URL="http://${NEXTSEEK_HOSTNAME}"' in example


def test_local_settings_template_prod_overlay_suppresses_internal_url() -> None:
    """The PROD ChatConfig overlay must not be shadowed by the internal URL.

    ChatConfig prefers NEXTSEEK_INTERNAL_BASE_URL over NEXTSEEK_BASE_URL, so
    when _PROD_OVERRIDES sets a prod NEXTSEEK_BASE_URL the overlay block must
    pop NEXTSEEK_INTERNAL_BASE_URL for the duration of that construction.
    """
    template = (
        _REPO_ROOT / "startup" / "templates" / "local_settings.py.template"
    ).read_text()
    assert 'os.environ.pop("NEXTSEEK_INTERNAL_BASE_URL", None)' in template
    # And it must restore it afterwards (tracked in the saved-env snapshot).
    assert '"NEXTSEEK_INTERNAL_BASE_URL"' in template.split("_prev_env")[1]


# --- Review follow-up FU1 (2026-07-07): the pop above is template-only — a
# stale hand-maintained local_settings.py without it lets the internal var
# silently point the PROD ChatConfig at the dev backend. The authoritative
# guard is passing the prod URL via config_map, which wins over ALL env
# resolution (ChatConfig applies config_map first; env fills gaps only).
# These tests EXECUTE the real template with a stub ChatConfig to pin that
# behavior — they cannot be satisfied by comment tweaks.


def _exec_real_template_with_stub(monkeypatch, *, overrides: dict, env: dict):
    """Execute the REAL local_settings template, operator-filled, against a
    stub ChatConfig that records (config_map, env-at-construction)."""
    import os
    import sys
    import types

    source = (
        _REPO_ROOT / "startup" / "templates" / "local_settings.py.template"
    ).read_text()
    for key, value in overrides.items():
        needle = f'"{key}": None,'
        assert needle in source, f"template lost the {key} override line"
        source = source.replace(needle, f'"{key}": {value!r},', 1)

    constructions: list[dict] = []

    class StubChatConfig:
        def __init__(self, config_map={}):
            constructions.append(
                {
                    "config_map": dict(config_map),
                    "env_internal": os.environ.get("NEXTSEEK_INTERNAL_BASE_URL"),
                    "env_base": os.environ.get("NEXTSEEK_BASE_URL"),
                }
            )

    fake_pkg = types.ModuleType("chat_nextseek")
    fake_config = types.ModuleType("chat_nextseek.config")
    fake_config.ChatConfig = StubChatConfig
    fake_pkg.config = fake_config
    monkeypatch.setitem(sys.modules, "chat_nextseek", fake_pkg)
    monkeypatch.setitem(sys.modules, "chat_nextseek.config", fake_config)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    exec(compile(source, "local_settings.rendered", "exec"), {})
    return constructions


def test_prod_overlay_passes_prod_url_via_config_map(monkeypatch) -> None:
    constructions = _exec_real_template_with_stub(
        monkeypatch,
        overrides={"NEXTSEEK_BASE_URL": "https://nextseek.example.edu/"},
        env={
            "NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000",
            "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000",
        },
    )
    assert len(constructions) == 2  # dev config, then PROD overlay config
    prod = constructions[1]
    # Authoritative guard: prod URL travels via config_map (trailing slash
    # normalized like _resolve_nextseek_base_url does).
    assert prod["config_map"]["NEXTSEEK_BASE_URL"] == "https://nextseek.example.edu"
    # Defense-in-depth pop still holds during the PROD construction ...
    assert prod["env_internal"] is None
    # ... and the overlay env was applied.
    assert prod["env_base"] == "https://nextseek.example.edu/"


def test_prod_overlay_restores_env_after_construction(monkeypatch) -> None:
    import os

    _exec_real_template_with_stub(
        monkeypatch,
        overrides={"NEXTSEEK_BASE_URL": "https://nextseek.example.edu"},
        env={
            "NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000",
            "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000",
        },
    )
    assert os.environ["NEXTSEEK_INTERNAL_BASE_URL"] == "http://127.0.0.1:8000"
    assert os.environ["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8000"


def test_dev_config_construction_carries_no_config_map(monkeypatch) -> None:
    constructions = _exec_real_template_with_stub(
        monkeypatch,
        overrides={"NEXTSEEK_BASE_URL": "https://nextseek.example.edu"},
        env={"NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000"},
    )
    assert constructions[0]["config_map"] == {}


def test_prod_overlay_without_url_override_keeps_internal_env(monkeypatch) -> None:
    """Overriding only credentials must not suppress the internal transport
    URL — the PROD config still self-calls this instance."""
    constructions = _exec_real_template_with_stub(
        monkeypatch,
        overrides={"API_USER": "produser"},
        env={"NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000"},
    )
    assert len(constructions) == 2
    prod = constructions[1]
    assert "NEXTSEEK_BASE_URL" not in prod["config_map"]
    assert prod["env_internal"] == "http://127.0.0.1:8000"


def test_laya_routing_defaults_are_off_in_template_and_example(tmp_path: Path) -> None:
    """JevLevROUTING: shadow and live are OFF in every env template; a box
    without these lines reads as off too (code defaults)."""
    rendered = _render_real_template(tmp_path, port=8042)
    example = (_REPO_ROOT / "docker" / "nextseek.env.example").read_text()
    for text in (rendered, example):
        assert 'NESSIE_LAYA_SHADOW="0"' in text.splitlines()
        assert 'NESSIE_LAYA_LIVE=""' in text.splitlines()
        assert 'LAYA_API_KEY="SET_IN_LOCAL_ENV"' in text.splitlines()


def test_laya_env_and_models_are_gitignored() -> None:
    lines = (_REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "docker/laya.env" in lines
    assert "docker/laya/models/" in lines


def test_ensure_seek_nginx_conf_creates_file_and_keeps_content(tmp_path: Path) -> None:
    p = ensure_seek_nginx_conf(tmp_path)
    assert p.is_file() and p.stat().st_mode & 0o777 == 0o666
    p.write_text("x")
    ensure_seek_nginx_conf(tmp_path)
    assert p.read_text() == "x"


def test_ensure_seek_nginx_conf_replaces_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "docker" / "seek-nginx.conf").mkdir(parents=True)
    assert ensure_seek_nginx_conf(tmp_path).is_file()


# --- #103 Task 1: install keeps the two Django host keys an operator set by hand.
# The template used to write DJANGO_ALLOWED_HOSTS as a literal and CSRF from the
# port alone, so every re-run of install reset a box's public host and origin.


def _repo_with_rendered_env(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "docker" / "nextseek.env").write_text(body)
    return repo


def test_read_rendered_value_reads_quoted_and_unquoted(tmp_path: Path) -> None:
    repo = _repo_with_rendered_env(
        tmp_path, 'DJANGO_ALLOWED_HOSTS="a.example.org 127.0.0.1"\nOTHER=x y\n'
    )
    assert read_rendered_value(repo, "DJANGO_ALLOWED_HOSTS") == "a.example.org 127.0.0.1"
    assert read_rendered_value(repo, "OTHER") == "x y"
    assert read_rendered_value(repo, "MISSING") is None


def test_read_rendered_value_last_line_wins_like_compose(tmp_path: Path) -> None:
    repo = _repo_with_rendered_env(
        tmp_path, 'DJANGO_ALLOWED_HOSTS="old.example.org"\nDJANGO_ALLOWED_HOSTS="new.example.org"\n'
    )
    assert read_rendered_value(repo, "DJANGO_ALLOWED_HOSTS") == "new.example.org"


def test_read_rendered_value_drops_an_inline_comment(tmp_path: Path) -> None:
    repo = _repo_with_rendered_env(
        tmp_path, 'DJANGO_ALLOWED_HOSTS="a.example.org localhost" # space-separated\n'
    )
    assert read_rendered_value(repo, "DJANGO_ALLOWED_HOSTS") == "a.example.org localhost"


def test_half_rendered_value_is_not_kept(tmp_path: Path) -> None:
    repo = _repo_with_rendered_env(
        tmp_path,
        'DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS}"\n'
        'DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS}"\n',
    )
    assert read_rendered_value(repo, "DJANGO_ALLOWED_HOSTS") is None
    assert read_rendered_value(repo, "DJANGO_CSRF_TRUSTED_ORIGINS") is None


def test_fresh_install_host_defaults() -> None:
    v = default_values(nextseek_port=8000, seek_port=3000)
    assert v.django_allowed_hosts == "127.0.0.1 localhost"
    assert v.django_csrf_trusted_origins == csrf_origins_for_port(8000)


def test_given_allowed_hosts_wins_over_the_default() -> None:
    v = default_values(8000, 3000, kept={"DJANGO_ALLOWED_HOSTS": "nextseek.example.org 127.0.0.1 localhost"})
    assert v.django_allowed_hosts == "nextseek.example.org 127.0.0.1 localhost"


def test_kept_csrf_is_kept_as_is_on_the_same_port() -> None:
    kept = "https://nextseek.example.org http://127.0.0.1:8000 http://localhost:8000"
    v = default_values(8000, 3000, kept={"DJANGO_CSRF_TRUSTED_ORIGINS": kept})
    assert v.django_csrf_trusted_origins == kept


def test_kept_csrf_gains_the_new_port_loopback_pair() -> None:
    kept = "https://nextseek.example.org http://127.0.0.1:8000 http://localhost:8000"
    v = default_values(8001, 3001, kept={"DJANGO_CSRF_TRUSTED_ORIGINS": kept})
    assert v.django_csrf_trusted_origins.split() == [
        "https://nextseek.example.org",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]


def test_real_template_renders_kept_host_keys(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "startup" / "templates").mkdir(parents=True)
    (repo / "startup" / "templates" / "nextseek.env.template").write_text(
        (_REPO_ROOT / "startup" / "templates" / "nextseek.env.template").read_text()
    )
    render_nextseek_env(
        repo,
        default_values(
            8000, 3000,
            kept={
                "DJANGO_ALLOWED_HOSTS": "nextseek.example.org 127.0.0.1 localhost",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://nextseek.example.org",
            },
        ),
    )
    rendered = (repo / "docker" / "nextseek.env").read_text()
    assert 'DJANGO_ALLOWED_HOSTS="nextseek.example.org 127.0.0.1 localhost"' in rendered
    assert (
        'DJANGO_CSRF_TRUSTED_ORIGINS="https://nextseek.example.org '
        'http://127.0.0.1:8000 http://localhost:8000"'
    ) in rendered


# --- #103 Task 4: install keeps every value already on disk. What it re-renders
# on purpose: a port-derived value install wrote for the old port, a line naming
# a pre-NessieAI path, a forbidden key, and a placeholder.

_KEPT_HEADER = "# Kept from the previous docker/nextseek.env by ./startup.sh install"


def _box(tmp_path: Path, db_env: str | None = None) -> Path:
    """A tmp checkout with the real templates."""
    repo = tmp_path / "box"
    (repo / "docker").mkdir(parents=True)
    (repo / "dmac").mkdir()
    shutil.copytree(_REPO_ROOT / "startup" / "templates", repo / "startup" / "templates")
    if db_env is not None:
        (repo / "docker" / "db.env").write_text(db_env)
    return repo


def _install(repo: Path, port: int = 8000, previous: dict | None = None) -> ConfigValues:
    """Install's step 4: read the files back, then render over them."""
    values = default_values(port, port - 5000, kept=kept_values(repo), previous_ports=previous)
    render_db_env(repo, values)
    render_nextseek_env(repo, values)
    return values


def _set(path: Path, **lines: str) -> None:
    """Hand-edit: KEY=<raw text> in place of the key's line."""
    out = [
        f"{line.split('=', 1)[0]}={lines[line.split('=', 1)[0]]}"
        if line.split("=", 1)[0] in lines else line
        for line in path.read_text().splitlines()
    ]
    path.write_text("\n".join(out) + "\n")


def _entries(path: Path) -> dict[str, tuple[str, str]]:
    return {p[0]: (p[1], p[2]) for p in map(parse_line, path.read_text().splitlines()) if p}


def test_read_env_and_read_rendered_value_read_a_line_the_same_way(tmp_path: Path) -> None:
    repo = _repo_with_rendered_env(
        tmp_path, 'DJANGO_ALLOWED_HOSTS="a.example.org localhost" # Space-separated\nOTHER = x\n'
    )
    env = read_env(repo / "docker" / "nextseek.env")
    assert env["DJANGO_ALLOWED_HOSTS"] == read_rendered_value(repo, "DJANGO_ALLOWED_HOSTS")
    assert env["OTHER"] == read_rendered_value(repo, "OTHER") == "x"


def test_fresh_install_renders_the_demo_values_and_keeps_nothing(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    values = _install(repo)
    db = (repo / "docker" / "db.env").read_text()
    nx = (repo / "docker" / "nextseek.env").read_text()
    assert f'MYSQL_ROOT_PASSWORD="{values.mysql_root_password}"' in db.splitlines()
    assert values.mysql_root_password not in _OLD
    assert f"NEXTSEEK_NEO4J_PASSWORD='{values.neo4j_password}'" in nx.splitlines()
    assert f"SESSION_DB_PASSWORD='{values.mysql_password}'" in nx.splitlines()
    assert "Kept from" not in db + nx
    assert read_env(repo / "docker" / "nextseek.env")["DJANGO_ALLOWED_HOSTS"] == "127.0.0.1 localhost"
    # Wave 1's comment for the lineage timeout stays a comment.
    assert "# NEO4J_TIMEOUT_SECONDS=5" in nx.splitlines()


def test_reinstall_keeps_every_hand_set_value(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    nx = repo / "docker" / "nextseek.env"
    _set(
        nx,
        GCP_API_KEY='"test-key-not-real"',
        NEXTSEEK_CC_MAX_BUDGET_USD='"3.00"',
        CATALOG_FILE='"/app/logs/x.json"',
        DJANGO_SECRET_KEY='"kept-secret-key"',
    )
    extra = ["NEXTSEEK_SERVER=gunicorn", "NEXTSEEK_GRAPH_SYNC_STUDY_LINKS=follow", "NEO4J_TIMEOUT_SECONDS=10"]
    nx.write_text(nx.read_text() + "\n".join(extra) + "\n")

    values = _install(repo)

    lines = nx.read_text().splitlines()
    for line in ('GCP_API_KEY="test-key-not-real"', 'NEXTSEEK_CC_MAX_BUDGET_USD="3.00"',
                 'CATALOG_FILE="/app/logs/x.json"', 'DJANGO_SECRET_KEY="kept-secret-key"'):
        assert line in lines
    assert values.django_secret_key == "kept-secret-key"
    # A key on disk that the template does not name goes after one header, in its old order.
    assert lines[lines.index(_KEPT_HEADER) + 1:] == extra


def test_reinstall_keeps_rotated_passwords_in_all_three_files(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    _set(repo / "docker" / "db.env", MYSQL_ROOT_PASSWORD="'root-rotated'", MYSQL_PASSWORD='"app-rotated"')
    _set(repo / "docker" / "nextseek.env", NEXTSEEK_NEO4J_PASSWORD="'neo-rotated'")

    values = _install(repo)
    render_root_env(repo, {"NEXTSEEK_PORT": "8000"}, neo4j_password=values.neo4j_password)

    db = read_env(repo / "docker" / "db.env")
    assert (db["MYSQL_ROOT_PASSWORD"], db["MYSQL_PASSWORD"]) == ("root-rotated", "app-rotated")
    assert read_env(repo / "docker" / "nextseek.env")["NEXTSEEK_NEO4J_PASSWORD"] == "neo-rotated"
    assert read_env(repo / ".env")["NEO4J_PASSWORD"] == "neo-rotated"
    assert (values.mysql_root_password, values.mysql_password) == ("root-rotated", "app-rotated")


def test_quoted_password_with_special_characters_survives(tmp_path: Path) -> None:
    pw = 'p$ss #w"rd\\x y'
    repo = _box(tmp_path)
    _install(repo)
    _set(repo / "docker" / "db.env", MYSQL_ROOT_PASSWORD=f"'{pw}'", MYSQL_PASSWORD=f"'{pw}'")
    _set(repo / "docker" / "nextseek.env", NEXTSEEK_NEO4J_PASSWORD=f"'{pw}'")
    db_before = (repo / "docker" / "db.env").read_text()

    values = _install(repo)
    render_root_env(repo, {}, neo4j_password=values.neo4j_password)

    assert (values.mysql_root_password, values.mysql_password, values.neo4j_password) == (pw, pw, pw)
    assert (repo / "docker" / "db.env").read_text() == db_before
    assert _entries(repo / "docker" / "nextseek.env")["NEXTSEEK_NEO4J_PASSWORD"] == (pw, "'")
    # Compose reads $$ in the root .env as one $.
    assert read_env(repo / ".env")["NEO4J_PASSWORD"] == pw.replace("$", "$$")

    # nextseek.env deleted to start it fresh: the lines that carry the MySQL
    # password take the kept one, single-quoted so compose reads it literally.
    (repo / "docker" / "nextseek.env").unlink()
    _install(repo)
    entries = _entries(repo / "docker" / "nextseek.env")
    for key in ("SESSION_DB_PASSWORD", "MYSQL_PROD_PASSWORD", "MYSQL_DEV_PASSWORD"):
        assert entries[key] == (pw, "'")


def test_a_dollar_outside_single_quotes_is_refused_by_name(tmp_path: Path) -> None:
    repo = _box(tmp_path, db_env='MYSQL_ROOT_PASSWORD="ab$cd"\n')
    with pytest.raises(UnsafeKeptValue) as raised:
        kept_values(repo)
    assert "MYSQL_ROOT_PASSWORD" in str(raised.value)
    assert "ab$cd" not in str(raised.value)


def test_interpolation_lines_survive_byte_for_byte(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    _set(repo / "docker" / "nextseek.env", MYSQL_HOST_PROD="$OTHER_HOST")
    _install(repo)
    lines = (repo / "docker" / "nextseek.env").read_text().splitlines()
    assert "SESSION_DB_HOST=$MYSQL_HOST" in lines
    assert "MYSQL_HOST_PROD=$OTHER_HOST" in lines
    assert 'NEXTSEEK_BASE_URL="http://$NEXTSEEK_HOSTNAME"' in lines
    assert "NEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD" in lines


def test_placeholder_or_half_rendered_value_is_not_kept_over_a_new_default(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    _set(repo / "docker" / "nextseek.env", NEXTSEEK_INTERNAL_BASE_URL='"${NEXTSEEK_INTERNAL_BASE_URL}"')
    template = repo / "startup" / "templates" / "nextseek.env.template"
    template.write_text(
        template.read_text().replace('LAYA_API_KEY="SET_IN_LOCAL_ENV"', 'LAYA_API_KEY="new-default"')
    )
    _install(repo)
    nx = read_env(repo / "docker" / "nextseek.env")
    assert nx["LAYA_API_KEY"] == "new-default"
    assert nx["NEXTSEEK_INTERNAL_BASE_URL"] == "http://127.0.0.1:8000"


def test_duplicate_key_keeps_the_last_occurrence(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    nx = repo / "docker" / "nextseek.env"
    nx.write_text(
        nx.read_text()
        + 'CATALOG_FILE="/app/logs/last.json"\nNEXTSEEK_SERVER=daphne\nNEXTSEEK_SERVER=gunicorn\n'
    )
    _install(repo)
    lines = nx.read_text().splitlines()
    assert [ln for ln in lines if ln.startswith("CATALOG_FILE=")] == ['CATALOG_FILE="/app/logs/last.json"']
    assert [ln for ln in lines if ln.startswith("NEXTSEEK_SERVER=")] == ["NEXTSEEK_SERVER=gunicorn"]


def test_stale_nessie_path_and_forbidden_keys_are_not_kept(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    nx = repo / "docker" / "nextseek.env"
    _set(nx, CATALOG_FILE='"/app/chat_nextseek/agent_model_catalog.json"')
    nx.write_text(nx.read_text() + 'DMAC_ROUTE_CAPABILITIES_FILE="/app/NessieAI/x.json"\n')
    _install(repo)
    assert read_env(nx)["CATALOG_FILE"] == "/app/NessieAI/chat_nextseek/agent_model_catalog.json"
    assert "DMAC_ROUTE_CAPABILITIES_FILE" not in nx.read_text()


def test_reinstall_on_a_new_port_moves_the_loopback_values(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo, 8000)
    nx = repo / "docker" / "nextseek.env"
    _set(nx, DJANGO_CSRF_TRUSTED_ORIGINS='"https://nextseek.example.org http://127.0.0.1:8000 http://localhost:8000"')

    _install(repo, 8001, previous={"nextseek": 8000, "seek": 3000})

    env = read_env(nx)
    assert env["NEXTSEEK_HOSTNAME"] == "127.0.0.1:8001"
    assert env["DJANGO_CSRF_TRUSTED_ORIGINS"].split() == [
        "https://nextseek.example.org", "http://127.0.0.1:8001", "http://localhost:8001",
    ]
    # A hand-set public host is not install's old-port value, so it stays.
    _set(nx, NEXTSEEK_HOSTNAME='"nextseek.example.org"')
    _install(repo, 8002, previous={"nextseek": 8001, "seek": 3001})
    assert read_env(nx)["NEXTSEEK_HOSTNAME"] == "nextseek.example.org"


def test_reinstall_twice_is_idempotent(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    nx = repo / "docker" / "nextseek.env"
    _set(nx, GCP_API_KEY="'test-key-not-real'  # filled by hand")
    nx.write_text(nx.read_text() + "NEXTSEEK_SERVER=gunicorn\n")
    _install(repo)
    first = {p.name: p.read_text() for p in (repo / "docker").iterdir()}
    _install(repo)
    assert {p.name: p.read_text() for p in (repo / "docker").iterdir()} == first
    assert first["nextseek.env"].count(_KEPT_HEADER) == 1


def test_reinstall_keeps_hand_set_lines_in_the_root_env(tmp_path: Path) -> None:
    """DEPLOYMENT.md has a box set its memory caps and SEEK's worker count in the
    root .env. A re-install rewrites install's own keys there and keeps every
    other line as written; the allowlist still keeps compose_env's secrets out."""
    root = tmp_path / ".env"
    root.write_text(
        'COMPOSE_PROJECT_NAME="old"\nNEO4J_PASSWORD="old"\n'
        "SEEK_MEMORY=20G\nSEEK_PUMA_WORKERS=8  # this box\n"
    )
    compose_env = {
        "COMPOSE_PROJECT_NAME": "nextseek", "NEXTSEEK_PORT": "8000",
        "AWS_BEARER_TOKEN_BEDROCK": "must-not-copy",
    }

    render_root_env(tmp_path, compose_env, neo4j_password="neo")
    first = root.read_text()
    render_root_env(tmp_path, compose_env, neo4j_password="neo")

    assert read_env(root) == {
        "COMPOSE_PROJECT_NAME": "nextseek", "NEXTSEEK_PORT": "8000", "NEO4J_PASSWORD": "neo",
        "SEEK_MEMORY": "20G", "SEEK_PUMA_WORKERS": "8",
    }
    assert "SEEK_PUMA_WORKERS=8  # this box" in first
    assert root.read_text() == first


def test_preview_names_what_a_reinstall_would_change_and_writes_nothing(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    _install(repo)
    nx = repo / "docker" / "nextseek.env"
    _set(nx, GCP_API_KEY='"test-key-not-real"', CATALOG_FILE='"/app/chat_nextseek/agent_model_catalog.json"')
    nx.write_text(nx.read_text() + "NEXTSEEK_SERVER=gunicorn\n")
    save_instance(repo, InstanceState(
        name="box", prefix="", ports={"nextseek": 8000, "seek": 3000},
        compose_project_name="nextseek", created="2026-10-13T00:00:00",
    ))
    before = {p.name: p.read_text() for p in (repo / "docker").iterdir()}

    got = preview_changed_keys(repo)

    assert got == {
        "rerendered": ["CATALOG_FILE"],
        "kept_differs_from_template": ["GCP_API_KEY", "NEXTSEEK_SERVER"],
    }
    assert "test-key-not-real" not in repr(got)
    assert {p.name: p.read_text() for p in (repo / "docker").iterdir()} == before


def test_install_leaves_an_existing_local_settings_alone(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    values = default_values(8000, 3000)
    path = render_local_settings(repo, values)
    assert path == repo / "dmac" / "local_settings.py"
    hand = path.read_text().replace('"API_USER": None', '"API_USER": "prod-user"')
    path.write_text(hand)

    written = render_local_settings(repo, values)

    assert written == repo / "dmac" / "local_settings.py.new"
    assert path.read_text() == hand
    assert written.read_text() == (repo / "startup" / "templates" / "local_settings.py.template").read_text()


def test_an_unchanged_local_settings_gets_no_new_copy(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    render_local_settings(repo, default_values(8000, 3000))
    assert render_local_settings(repo, default_values(8000, 3000)) == repo / "dmac" / "local_settings.py"
    assert not (repo / "dmac" / "local_settings.py.new").exists()


def test_the_new_local_settings_copy_never_reaches_git_or_the_image() -> None:
    assert "local_settings.py.new" in (_REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "dmac/local_settings.py*" in (_REPO_ROOT / ".dockerignore").read_text().splitlines()


# the spawner token pair (W9 part 1): one value in two env files

def _spawner_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "docker" / "nextseek.env").write_text('DJANGO_SECRET_KEY="k"\n')
    return repo


def test_render_spawner_env_writes_the_same_token_to_both_files(tmp_path: Path) -> None:
    from startup.steps.config import render_spawner_env

    repo = _spawner_repo(tmp_path)
    out = render_spawner_env(repo)

    app = read_env(repo / "docker" / "nextseek.env")
    spawner = read_env(out)
    assert out == repo / "docker" / "cc-spawner.env"
    assert len(app["NEXTSEEK_SPAWNER_TOKEN"]) >= 40
    assert spawner == {"SPAWNER_TOKEN": app["NEXTSEEK_SPAWNER_TOKEN"]}
    assert app["DJANGO_SECRET_KEY"] == "k", "the rest of the file is untouched"
    assert (out.stat().st_mode & 0o777) == 0o600


def test_render_spawner_env_keeps_the_token_on_a_rerun(tmp_path: Path) -> None:
    from startup.steps.config import render_spawner_env

    repo = _spawner_repo(tmp_path)
    render_spawner_env(repo)
    first = read_env(repo / "docker" / "nextseek.env")["NEXTSEEK_SPAWNER_TOKEN"]
    render_spawner_env(repo)
    assert read_env(repo / "docker" / "nextseek.env")["NEXTSEEK_SPAWNER_TOKEN"] == first
    assert read_env(repo / "docker" / "cc-spawner.env")["SPAWNER_TOKEN"] == first
    assert (repo / "docker" / "nextseek.env").read_text().count("NEXTSEEK_SPAWNER_TOKEN") == 1


def test_render_spawner_env_repairs_a_missing_spawner_file_from_the_app_value(tmp_path: Path) -> None:
    from startup.steps.config import render_spawner_env

    repo = _spawner_repo(tmp_path)
    (repo / "docker" / "nextseek.env").write_text("NEXTSEEK_SPAWNER_TOKEN=abc123\n")
    render_spawner_env(repo)
    assert read_env(repo / "docker" / "cc-spawner.env") == {"SPAWNER_TOKEN": "abc123"}
