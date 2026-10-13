"""Render startup-managed env files and Django settings."""
from __future__ import annotations

import dataclasses
import os
import re
import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

from startup.lib import env_registry
from startup.lib.env import parse_line, read_env, read_env_text, write_env
from startup.lib.instance import load_instance
from startup.lib.layout import legacy_proxy_secret_env, proxy_secret_env


@dataclass
class ConfigValues:
    mysql_root_password: str
    mysql_password: str
    neo4j_password: str
    django_secret_key: str
    django_csrf_trusted_origins: str
    nextseek_port: int
    seek_port: int
    django_allowed_hosts: str = "127.0.0.1 localhost"
    # Browser-reachable SEEK base URL. Per-instance: dev and prod front SEEK on
    # real hostnames; a laptop install reaches it on the published port. Defaults
    # via default_values(); resolve_seek_public_url() decides the actual value.
    seek_public_url: str = ""
    # The ports the files on disk were rendered for (startup/.instance.json before
    # this install). A value install derived from them is re-rendered for the new ones.
    previous_ports: dict[str, int] | None = None


DEFAULT_SEEK_PUBLIC_URL_HOST = "http://localhost"

# A bare origin: scheme://host[:port]. No path -- SEEK's admin controller strips
# paths from this setting, and both consumers append their own ("/projects/1").
_SEEK_PUBLIC_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?::\d+)?$"
)


class InvalidSeekPublicUrl(ValueError):
    """An operator-supplied SEEK public URL is not a bare scheme://host[:port]."""


def _valid_seek_public_url(value: str | None) -> bool:
    return bool(value) and bool(_SEEK_PUBLIC_URL_RE.match(value))


def read_rendered_value(repo_root: Path, key: str) -> str | None:
    """Read one value back out of an already-rendered docker/nextseek.env.

    Read with startup.lib.env's parser, as compose reads it: the last line for
    the key wins, and an inline comment is dropped. None for a missing, empty
    or placeholder value.
    """
    raw = read_env(repo_root / "docker" / "nextseek.env").get(key, "").strip()
    return raw if raw and not _is_placeholder(raw) else None


def _is_placeholder(value: str) -> bool:
    """A template placeholder, or a half-rendered ${...}: never a value to keep.

    An empty value is not one: it can be an operator's deliberate "off"."""
    return (value != "" and value in env_registry.PLACEHOLDERS) or "${" in value


# What install reads back into ConfigValues before it renders: key -> (the file
# it is read from, the ConfigValues field it feeds). Every file and install's own
# database steps then take the password from one place, so they cannot disagree (#103).
_KEPT_FROM = {
    "MYSQL_ROOT_PASSWORD": ("db.env", "mysql_root_password"),
    "MYSQL_PASSWORD": ("db.env", "mysql_password"),
    "NEXTSEEK_NEO4J_PASSWORD": ("nextseek.env", "neo4j_password"),
    "DJANGO_SECRET_KEY": ("nextseek.env", "django_secret_key"),
    "DJANGO_ALLOWED_HOSTS": ("nextseek.env", "django_allowed_hosts"),
    "DJANGO_CSRF_TRUSTED_ORIGINS": ("nextseek.env", "django_csrf_trusted_origins"),
}


class UnsafeKeptValue(ValueError):
    """A value install would keep cannot be read back the way compose reads it."""


def _env_lines(path: Path) -> dict[str, tuple[str, str, str]]:
    """{key: (line, value, quote)} of a rendered env file, the last line per key."""
    if not path.exists():
        return {}
    out: dict[str, tuple[str, str, str]] = {}
    for line in path.read_text().splitlines():
        parsed = parse_line(line)
        if parsed:
            out[parsed[0]] = (line, parsed[1], parsed[2])
    return out


def kept_values(repo_root: Path) -> dict[str, str]:
    """The credentials and host keys already on disk, for default_values(kept=...).

    Raises UnsafeKeptValue, naming the key and never the value, for a ``$``
    outside single quotes: compose expands it, so the password the containers
    were given is not the text in the file, and install's own database steps
    would use the wrong one.
    """
    files = {name: _env_lines(repo_root / "docker" / name) for name in ("db.env", "nextseek.env")}
    kept = {}
    for key, (name, _field) in _KEPT_FROM.items():
        _line, value, quote = files[name].get(key, ("", "", ""))
        if not value or _is_placeholder(value):
            continue
        if "$" in value and quote != "'":
            raise UnsafeKeptValue(
                f"docker/{name}: {key} holds a $ outside single quotes, which compose "
                f"expands. Write it as {key}='...' (single quotes are literal), then re-run"
            )
        kept[key] = value
    return kept


def read_rendered_seek_public_url(repo_root: Path) -> str | None:
    return read_rendered_value(repo_root, "SEEK_PUBLIC_URL")


def resolve_seek_public_url(
    repo_root: Path,
    explicit: str | None,
    instance_value: str | None,
    seek_port: int,
) -> str:
    """Resolve the browser-reachable SEEK base URL for this instance.

    Precedence (never-clobber):
      1. explicit --seek-public-url flag
      2. an existing rendered docker/nextseek.env  <- preserves hand-set values
      3. the value stored in startup/.instance.json
      4. http://localhost:<seek_port>              <- correct for a laptop only

    Only the explicit flag is validated strictly; a malformed value already on
    disk falls through rather than aborting an install.
    """
    if explicit is not None:
        if not _valid_seek_public_url(explicit):
            raise InvalidSeekPublicUrl(
                f"--seek-public-url must be scheme://host[:port] with no path; got {explicit!r}"
            )
        return explicit

    rendered = read_rendered_seek_public_url(repo_root)
    if _valid_seek_public_url(rendered):
        return rendered  # type: ignore[return-value]

    if _valid_seek_public_url(instance_value):
        return instance_value  # type: ignore[return-value]

    return f"{DEFAULT_SEEK_PUBLIC_URL_HOST}:{seek_port}"


def _generate_secret_key(length: int = 64) -> str:
    # Excludes `$`, `\\`, `"`, `'`, `` ` ``, and `#` so the rendered DJANGO_SECRET_KEY in
    # docker/nextseek.env is never re-interpolated or quote-corrupted when docker
    # compose loads the env file. 64 chars across 76 symbols ≈ 400 bits of entropy.
    alphabet = string.ascii_letters + string.digits + "!@%^&*()-_=+:.<>?"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def csrf_origins_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port} http://localhost:{port}"


def _with_loopback(origins: str, port: int) -> str:
    """Kept origins plus this port's loopback pair, deduped, order kept.

    A re-install can move the port, and a kept list without the new pair would
    lock out the loopback login.
    """
    return " ".join(dict.fromkeys(origins.split() + csrf_origins_for_port(port).split()))


def default_values(
    nextseek_port: int,
    seek_port: int,
    seek_public_url: str | None = None,
    kept: Mapping[str, str] | None = None,
    previous_ports: Mapping[str, int] | None = None,
) -> ConfigValues:
    # `kept` is kept_values(): what install read back from the files on disk, so
    # a re-run keeps a box's credentials, secret key, public host and origin (#103).
    kept = kept or {}
    origins = kept.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
    if previous_ports and previous_ports.get("nextseek") not in (None, nextseek_port):
        # The port moved: install's loopback pair for the old one moves with it.
        old_pair = csrf_origins_for_port(previous_ports["nextseek"]).split()
        origins = " ".join(o for o in origins.split() if o not in old_pair)
    return ConfigValues(
        mysql_root_password=kept.get("MYSQL_ROOT_PASSWORD") or "seek_root",
        mysql_password=kept.get("MYSQL_PASSWORD") or "seek_db_password",
        neo4j_password=kept.get("NEXTSEEK_NEO4J_PASSWORD") or "demopassword",
        django_secret_key=kept.get("DJANGO_SECRET_KEY") or _generate_secret_key(),
        django_csrf_trusted_origins=_with_loopback(origins, nextseek_port),
        django_allowed_hosts=kept.get("DJANGO_ALLOWED_HOSTS") or "127.0.0.1 localhost",
        previous_ports=dict(previous_ports) if previous_ports else None,
        nextseek_port=nextseek_port,
        seek_port=seek_port,
        # Callers that resolve a per-instance value pass it in; the fallback keeps
        # a laptop install correct (and tracks a port-offset install, which SEEK's
        # own hardcoded :3000 default does not).
        seek_public_url=seek_public_url or f"{DEFAULT_SEEK_PUBLIC_URL_HOST}:{seek_port}",
    )


def _render(template_path: Path, values: ConfigValues) -> str:
    text = template_path.read_text()
    substitutions = {
        "MYSQL_ROOT_PASSWORD": values.mysql_root_password,
        "MYSQL_PASSWORD": values.mysql_password,
        "NEO4J_PASSWORD": values.neo4j_password,
        "DJANGO_SECRET_KEY": values.django_secret_key,
        "DJANGO_CSRF_TRUSTED_ORIGINS": values.django_csrf_trusted_origins,
        "DJANGO_ALLOWED_HOSTS": values.django_allowed_hosts,
        "NEXTSEEK_PORT": str(values.nextseek_port),
        "SEEK_PORT": str(values.seek_port),
        "SEEK_PUBLIC_URL": values.seek_public_url,
    }
    return Template(text).safe_substitute(substitutions)


# Lines whose value install read back from the same line (kept_values, or
# resolve_seek_public_url for the SEEK URL): key -> ConfigValues field. The old
# line stays unless install changed the value on purpose (the CSRF loopback pair,
# --seek-public-url).
_SELF_KEPT = {key: field for key, (_name, field) in _KEPT_FROM.items()} | {
    "SEEK_PUBLIC_URL": "seek_public_url"
}
_PORT_PLACEHOLDERS = ("${NEXTSEEK_PORT}", "${SEEK_PORT}")


def env_drop_keys(repo_root: Path) -> frozenset[str]:
    """Keys install re-renders whatever is on disk: a value naming a pre-NessieAI
    path, which install's own health check fails on, and a registry `forbidden` key."""
    # Imported here: validate imports this module.
    from startup.steps.validate import find_stale_nessie_env_lines

    stale = {key for _, key, _ in find_stale_nessie_env_lines(repo_root / "docker" / "nextseek.env")}
    forbidden = {key for key, entry in env_registry.load().items() if entry.forbidden}
    return frozenset(stale | forbidden)


def _render_keeping(repo_root: Path, name: str, values: ConfigValues) -> str:
    """Render templates/<name>.template, keeping every value already in docker/<name>.

    A template key already on disk keeps its old line verbatim (the last one when
    it is written twice), so compose reads exactly what it read before. Re-rendered
    instead: a key in env_drop_keys, a placeholder or half-rendered value, a port
    line still holding install's value for the previous ports, and a _SELF_KEPT
    line whose value install changed on purpose. Keys the template does not name
    follow the render under one header, in their old order. Rendering the result
    again gives the same bytes.
    """
    template = repo_root / "startup" / "templates" / f"{name}.template"
    old = _env_lines(repo_root / "docker" / name)
    drop = env_drop_keys(repo_root)
    port_keys = {
        p[0] for line in template.read_text().splitlines()
        if any(ph in line for ph in _PORT_PLACEHOLDERS) and (p := parse_line(line))
    }
    prev = values.previous_ports
    old_port_render = read_env_text(_render(template, dataclasses.replace(
        values, nextseek_port=prev.get("nextseek", values.nextseek_port),
        seek_port=prev.get("seek", values.seek_port),
    ))) if prev else {}

    def keep(key: str) -> bool:
        _line, value, _quote = old[key]
        if key in drop or _is_placeholder(value):
            return False
        if key in port_keys and value == old_port_render.get(key):
            return False
        # Against the intended value, not the rendered text: a template that
        # double-quotes a line would misread a value holding a quote.
        return key not in _SELF_KEPT or value == getattr(values, _SELF_KEPT[key])

    out: list[str] = []
    for line in _render(template, values).splitlines():
        parsed = parse_line(line)
        if parsed and parsed[0] in old and keep(parsed[0]):
            line = old[parsed[0]][0]
        out.append(line)
    named = {p[0] for p in map(parse_line, out) if p}
    extra = [line for key, (line, _v, _q) in old.items() if key not in named and key not in drop]
    if extra:
        out += ["", f"# Kept from the previous docker/{name} by ./startup.sh install", *extra]
    return "\n".join(out) + "\n"


def _write_keeping(repo_root: Path, name: str, values: ConfigValues) -> Path:
    output = repo_root / "docker" / name
    output.parent.mkdir(parents=True, exist_ok=True)
    # write_text, never a rename: the file keeps its mode.
    output.write_text(_render_keeping(repo_root, name, values))
    return output


def render_db_env(repo_root: Path, values: ConfigValues) -> Path:
    return _write_keeping(repo_root, "db.env", values)


def render_nextseek_env(repo_root: Path, values: ConfigValues) -> Path:
    return _write_keeping(repo_root, "nextseek.env", values)


def preview_changed_keys(
    repo_root: Path, values: ConfigValues | None = None
) -> dict[str, list[str]]:
    """What install would change in docker/db.env and docker/nextseek.env. Names only; writes nothing.

    ``rerendered``: keys on disk whose value install would replace.
    ``kept_differs_from_template``: keys install writes with a value a fresh
    install would not (hand-set, kept, or an older template's default).
    Without ``values`` it builds them as install would, with the ports in
    startup/.instance.json, never allocate_ports.
    """
    if values is None:
        state = load_instance(repo_root)
        ports = state.ports if state else {"nextseek": 8000, "seek": 3000}
        url = resolve_seek_public_url(
            repo_root, None, state.seek_public_url if state else None, ports["seek"]
        )
        values = default_values(ports["nextseek"], ports["seek"], url,
                                kept=kept_values(repo_root), previous_ports=ports)
    fresh_values = dataclasses.replace(
        default_values(values.nextseek_port, values.seek_port, values.seek_public_url),
        django_secret_key=values.django_secret_key,  # random per call, never a template default
    )
    changes: dict[str, list[str]] = {"rerendered": [], "kept_differs_from_template": []}
    for name in ("db.env", "nextseek.env"):
        template = repo_root / "startup" / "templates" / f"{name}.template"
        old = read_env(repo_root / "docker" / name)
        new = read_env_text(_render_keeping(repo_root, name, values))
        fresh = read_env_text(_render(template, fresh_values))
        changes["rerendered"] += [k for k, v in old.items() if new.get(k) != v]
        changes["kept_differs_from_template"] += [k for k, v in new.items() if fresh.get(k) != v]
    return changes


def ensure_seek_nginx_conf(repo_root: Path) -> Path:
    """Make docker/seek-nginx.conf exist as a file (chmod 666), never overwriting it.

    Compose bind-mounts it into `seek`; if absent Docker creates a directory there
    and SEEK crash-loops. SEEK's entrypoint (uid 33) rewrites it on every start, so
    an empty file is enough. An empty directory left by an earlier crash is replaced.
    """
    path = repo_root / "docker" / "seek-nginx.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        path.rmdir()
    path.touch(exist_ok=True)
    path.chmod(0o666)
    return path


def render_local_settings(repo_root: Path, values: ConfigValues) -> Path:
    """Write dmac/local_settings.py, or leave an existing one alone (#103).

    An existing file is the operator's: it may hold a filled PROD block or
    settings knobs. When the template's render differs from it, the render goes
    to dmac/local_settings.py.new beside it, for a diff by hand. Returns the
    path written, or the existing file when nothing changed.
    """
    template = repo_root / "startup" / "templates" / "local_settings.py.template"
    output = repo_root / "dmac" / "local_settings.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    text = _render(template, values)
    if output.exists():
        if output.read_text() == text:
            return output
        output = output.with_name("local_settings.py.new")
    output.write_text(text)
    return output


def render_proxy_secret_env(
    repo_root: Path,
    source_env: Mapping[str, str] | None = None,
) -> Path:
    """Render the bedrock proxy env_file required by docker compose.

    The institutional Bedrock token is runtime-only. If the operator exported it
    before running startup, copy it into the proxy env_file; otherwise preserve
    whatever token is already on disk (a hand-filled value must never be
    clobbered back to empty by a re-run of install); otherwise write an empty
    token so compose can still create the service and paid CC calls remain
    disabled until the file is filled in. The file is written mode 0600 since
    it holds an institutional Bedrock token.

    The path is ``startup.lib.layout.PROXY_SECRET_ENV``. A token still sitting
    at the pre-NessieAI location is read as the last fallback, so a re-run on a
    box that has not moved the file yet carries its token across instead of
    rendering an empty one. That file is only read, never written or removed.
    """
    source = source_env if source_env is not None else os.environ
    output = proxy_secret_env(repo_root)
    existing = read_env(output)
    legacy = read_env(legacy_proxy_secret_env(repo_root))
    # Precedence: operator env > existing hand-filled file > pre-move file > empty.
    # Re-running install must never clobber a filled token back to empty (D2).
    token = (
        source.get("AWS_BEARER_TOKEN_BEDROCK", "")
        or existing.get("AWS_BEARER_TOKEN_BEDROCK", "")
        or legacy.get("AWS_BEARER_TOKEN_BEDROCK", "")
    )
    region = (
        source.get("AWS_REGION")
        or source.get("AWS_DEFAULT_REGION")
        or existing.get("AWS_REGION")
        or legacy.get("AWS_REGION")
        or "us-east-1"
    )
    write_env(output, {"AWS_BEARER_TOKEN_BEDROCK": token, "AWS_REGION": region})
    output.chmod(0o600)  # institutional Bedrock token (D3)
    return output


def render_root_env(
    repo_root: Path,
    compose_env: Mapping[str, str],
    neo4j_password: str | None = None,
) -> Path:
    """Persist compose targeting vars for manual docker compose use.

    ``compose_env`` is filtered to ``ordered_keys`` so that a secret carried in
    the instance's environment can never leak into this file; keep it that way.

    ``neo4j_password`` is the one credential that must land here anyway.
    ``docker-compose.yml`` interpolates it with the mandatory
    ``${NEO4J_PASSWORD:?...}`` form, and compose resolves interpolation before
    it validates anything, so an unset value aborts *every* verb -- ``config``
    and ``ps`` included, not just ``up``. It is passed explicitly rather than
    read out of ``compose_env`` so the filter above stays a hard guarantee.

    It is sourced from the same ``ConfigValues`` that renders
    ``docker/nextseek.env``, so the password the app uses and the one seeding
    the server's ``NEO4J_AUTH`` come from a single place and cannot drift.
    (They previously could: ``NEO4J_AUTH`` only seeds on first volume creation,
    so a mismatch surfaces as an auth failure with nothing logged at compose
    time.) Callers that omit it keep the old secret-free behaviour.

    A line already in the file for any other key (a box's memory caps, SEEK's
    worker count) is kept as written, under one header (#103). It comes from
    the file, never from ``compose_env``, so the allowlist still holds.
    """
    ordered_keys = [
        "COMPOSE_PROJECT_NAME",
        "NEXTSEEK_PORT",
        "SEEK_PORT",
        "NEO4J_HTTP_PORT",
        "NEO4J_BOLT_PORT",
        "DB_PORT",
        "INSTANCE_PREFIX",
    ]
    values = {key: compose_env[key] for key in ordered_keys if key in compose_env}
    output = repo_root / ".env"
    owned = {*ordered_keys, "NEO4J_PASSWORD"}
    kept = [line for key, (line, _v, _q) in _env_lines(output).items() if key not in owned]
    if neo4j_password is not None:
        # Compose interpolates $ in .env even inside double quotes; $$ is a literal $.
        # Its other reader, nessie_venue.sh, applies compose's rules too; startup
        # never parses it back.
        values["NEO4J_PASSWORD"] = neo4j_password.replace("$", "$$")
    write_env(output, values)
    if kept:
        with output.open("a") as f:
            f.write("\n".join(["", "# Kept from the previous .env by ./startup.sh install", *kept, ""]))
    if neo4j_password is not None:
        output.chmod(0o600)  # now carries a credential
    return output
