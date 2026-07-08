"""Post-install health checks."""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

from startup.lib.docker_ops import compose_exec, DockerOpsError


@dataclass
class HealthResult:
    name: str
    ok: bool
    detail: str


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
    guarded = (
        'os.environ.pop("NEXTSEEK_INTERNAL_BASE_URL"' in settings_text
        or "config_map" in settings_text
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


def run_all_health_checks(
    ports: dict[str, int], repo_root: Path, env: dict[str, str]
) -> list[HealthResult]:
    results = [
        check_http("SEEK", f"http://localhost:{ports.get('seek', 3000)}"),
        check_http("NExtSEEK", f"http://localhost:{ports.get('nextseek', 8000)}"),
        check_http("Neo4j", f"http://localhost:{ports.get('neo4j_http', 7474)}"),
        run_django_check(repo_root, env),
        check_prod_overlay_guard(repo_root),
    ]
    return results
