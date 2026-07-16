"""Post-install health checks."""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from startup.lib.instance import load_instance
from startup.steps import seek_settings
from startup.steps.config import read_rendered_seek_public_url
from startup.lib.docker_ops import compose_exec, compose_ps_running, DockerOpsError
from startup.lib.env import read_env


@dataclass
class HealthResult:
    name: str
    ok: bool
    detail: str
    warn: bool = False


def check_http(name: str, url: str, timeout: float = 5.0) -> HealthResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.getcode()
        return HealthResult(name=name, ok=200 <= code < 400, detail=f"{url} → {code}")
    except Exception as exc:
        return HealthResult(name=name, ok=False, detail=f"{url} → {exc}")


def run_django_check(repo_root: Path, env: dict[str, str]) -> HealthResult:
    try:
        out = compose_exec(
            service="nextseek",
            command=["uv", "run", "manage.py", "check"],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError as exc:
        return HealthResult(name="django check", ok=False, detail=str(exc))
    return HealthResult(
        name="django check",
        ok=True,
        detail=out.strip().splitlines()[-1] if out.strip() else "exit 0",
    )


def check_prod_overlay_guard(repo_root: Path) -> HealthResult:
    """Flag a stale PROD overlay that the internal transport URL can shadow.

    Review follow-up FU1 (2026-07-07): ChatConfig prefers
    NEXTSEEK_INTERNAL_BASE_URL over NEXTSEEK_BASE_URL. The current
    local_settings template guards its PROD overlay (env pop + config_map),
    but a pre-guard hand-maintained dmac/local_settings.py has neither — on
    such a box, setting NEXTSEEK_INTERNAL_BASE_URL in docker/nextseek.env
    makes the admin PROD toggle silently self-call the DEV backend. Unsafe
    only when the env sets the var AND the overlay carries no guard at all.
    """
    name = "prod overlay guard"
    env_file = repo_root / "docker" / "nextseek.env"
    settings_file = repo_root / "dmac" / "local_settings.py"
    if not env_file.exists() or not settings_file.exists():
        return HealthResult(
            name=name, ok=True, detail="env/local_settings not rendered — nothing to check"
        )

    def _internal_var_set(text: str) -> bool:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() == "NEXTSEEK_INTERNAL_BASE_URL":
                if value.strip().strip("\"'"):
                    return True
        return False

    if not _internal_var_set(env_file.read_text()):
        return HealthResult(
            name=name, ok=True, detail="NEXTSEEK_INTERNAL_BASE_URL not set in nextseek.env"
        )
    settings_text = settings_file.read_text()
    if "_PROD_OVERRIDES" not in settings_text:
        return HealthResult(
            name=name, ok=True, detail="no PROD overlay in local_settings.py"
        )
    # Judge only the overlay block itself (everything from _PROD_OVERRIDES
    # on): a DEV-side ChatConfig(config_map=...) above it — or a comment
    # mentioning config_map — is not a guard. Accept either quote style for
    # a hand-ported pop.
    overlay_text = settings_text[settings_text.index("_PROD_OVERRIDES") :]
    guarded = bool(
        re.search(
            r"os\.environ\.pop\(\s*['\"]NEXTSEEK_INTERNAL_BASE_URL['\"]",
            overlay_text,
        )
        or re.search(r"ChatConfig\(\s*config_map\s*=", overlay_text)
    )
    if guarded:
        return HealthResult(
            name=name, ok=True, detail="PROD overlay carries the internal-URL guard"
        )
    return HealthResult(
        name=name,
        ok=False,
        detail=(
            "NEXTSEEK_INTERNAL_BASE_URL is set but dmac/local_settings.py "
            "predates the internal-URL guard (no env pop, no config_map): the "
            "PROD ChatConfig overlay would self-call the dev backend — "
            "regenerate the file from startup/templates/local_settings.py."
            "template (or port its overlay block) before enabling "
            "_PROD_OVERRIDES"
        ),
    )


def check_proxy_token(repo_root: Path) -> HealthResult:
    secret = repo_root / "docker" / "bedrock-proxy" / "proxy-secret.env"
    token = read_env(secret).get("AWS_BEARER_TOKEN_BEDROCK", "")
    if token:
        return HealthResult(name="bedrock proxy token", ok=True, detail="token present")
    return HealthResult(
        name="bedrock proxy token",
        ok=True,
        warn=True,
        detail=(
            "EMPTY — CC model calls are disabled. Fill "
            "docker/bedrock-proxy/proxy-secret.env, then "
            "`docker compose up -d --force-recreate bedrock-proxy`"
        ),
    )


def check_cc_services(repo_root: Path, env: dict[str, str]) -> HealthResult:
    wanted = ["bedrock-proxy", "nextseek-sidecar"]
    try:
        running = compose_ps_running(wanted, repo_root, env)
    except DockerOpsError as exc:
        return HealthResult(name="cc services", ok=False, detail=str(exc))
    missing = [s for s in wanted if s not in running]
    if missing:
        return HealthResult(
            name="cc services", ok=False, detail=f"not running: {', '.join(missing)}"
        )
    return HealthResult(
        name="cc services", ok=True, detail="bedrock-proxy + nextseek-sidecar running"
    )


def check_seek_url_consistency(
    repo_root: Path, state, env: dict[str, str]
) -> HealthResult:
    """Flag drift between the two SEEK-URL layers.

    Layer A -- docker/nextseek.env SEEK_PUBLIC_URL -- is how NExtSEEK builds
    browser-facing links TO SEEK. Layer B -- SEEK's DB-backed site_base_host --
    is how SEEK identifies ITSELF (its "SEEK ID", JSON-LD @id, sitemap). install()
    renders both from one stored per-instance value, but they can still diverge
    out of band: an admin changes SEEK's setting in its UI, or someone hand-edits
    the env. That divergence is exactly what produced the original bug -- correct
    links next to localhost identifiers -- so surface it here rather than let it
    be found in a browser.
    """
    name = "SEEK public URL"
    configured = getattr(state, "seek_public_url", "") or None
    rendered = read_rendered_seek_public_url(repo_root)

    try:
        in_seek = seek_settings.read_site_base_host(repo_root, env)
    except Exception as exc:  # DB down / stack not up: doctor still runs
        return HealthResult(
            name=name,
            ok=True,
            warn=True,
            detail=(
                f"could not read SEEK's site_base_host ({exc.__class__.__name__}); "
                f"configured={configured!r}, rendered={rendered!r}. Is the stack up?"
            ),
        )

    if in_seek is None:
        return HealthResult(
            name=name,
            ok=False,
            detail=(
                f"SEEK's site_base_host is not set -- SEEK is publishing identifiers on its "
                f"default http://localhost:3000, while NExtSEEK links to {rendered!r}. "
                "Re-run `./startup.sh install` to apply it."
            ),
        )

    values = {v for v in (configured, rendered, in_seek) if v}
    if len(values) > 1:
        return HealthResult(
            name=name,
            ok=False,
            detail=(
                f"drift: instance={configured!r}, docker/nextseek.env={rendered!r}, "
                f"SEEK site_base_host={in_seek!r}. NExtSEEK's links and SEEK's own "
                "identifiers disagree. Reconcile with "
                "`./startup.sh install --seek-public-url <url>` (SEEK's row is never "
                "overwritten by tooling -- change it in SEEK's admin UI if that is the wrong one)."
            ),
        )

    return HealthResult(name=name, ok=True, detail=f"{in_seek} (env, instance and SEEK agree)")


def run_all_health_checks(
    ports: dict[str, int], repo_root: Path, env: dict[str, str]
) -> list[HealthResult]:
    results = [
        check_http("SEEK", f"http://localhost:{ports.get('seek', 3000)}"),
        check_http("NExtSEEK", f"http://localhost:{ports.get('nextseek', 8000)}"),
        check_http("Neo4j", f"http://localhost:{ports.get('neo4j_http', 7474)}"),
        run_django_check(repo_root, env),
        check_prod_overlay_guard(repo_root),
        check_seek_url_consistency(repo_root, load_instance(repo_root), env),
        check_proxy_token(repo_root),
        check_cc_services(repo_root, env),
    ]
    return results
