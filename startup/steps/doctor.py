"""Read-only diagnostic for an existing install."""
from __future__ import annotations

import os
from pathlib import Path

from startup.lib.instance import InstanceState, load_instance
from startup.steps import prereqs, registry_push, validate

# Where ci/smoke/conftest.py looks for the smoke credentials, and the override it
# honours. Restated rather than imported: startup/ never imports ci/.
CI_ENV_VAR = "NEXTSEEK_CI_ENV"
DEFAULT_CI_ENV = Path.home() / ".config" / "nextseek" / "ci.env"
CI_CRED_KEYS = ("CI_SMOKE_USER", "CI_SMOKE_PASS")


def _ci_env_path() -> Path:
    override = os.environ.get(CI_ENV_VAR)
    return Path(override) if override else DEFAULT_CI_ENV


def check_ci_profile(state: InstanceState) -> tuple[str, bool, str]:
    """What profile this box declares to the smoke suite.

    Reported, never judged: all three values are legitimate, and an absent one is
    the documented default rather than a fault. What a reader needs is to see which
    of them is in force before they wonder why a route was skipped.
    """
    if state.ci_profile:
        return ("CI profile", True, f"{state.ci_profile} (startup/.instance.json)")
    return (
        "CI profile",
        True,
        "absent -> prod (fail closed). Set ci_profile in startup/.instance.json "
        "to widen this box; see startup/README.md.",
    )


def check_ci_credentials() -> tuple[str, bool, str]:
    """Whether the smoke suite has credentials here, and NEVER what they are.

    The file is opened only to learn which KEYS it names. No value is read into a
    variable, let alone into this string: doctor's output goes to a terminal, into
    scrollback and into pasted bug reports.

    Reported rather than failed, for the same reason as the profile: a box that
    does not run CI is not broken. It is still worth saying plainly, because a
    missing file is exactly what makes `./startup.sh rebuild` stop at the CI step
    with exit 2 rather than at anything to do with the rebuild.
    """
    path = _ci_env_path()
    if not path.is_file():
        return (
            "CI credentials",
            True,
            f"{path} absent -- ./startup.sh rebuild will exit 2 at its CI step; "
            f"place the file or rebuild with --no-ci",
        )
    named = {
        line.split("=", 1)[0].strip()
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing = [key for key in CI_CRED_KEYS if key not in named]
    if missing:
        return (
            "CI credentials",
            True,
            f"{path} present but does not name {', '.join(missing)} -- the smoke "
            f"suite will skip, and its readiness gate will exit 2",
        )
    return ("CI credentials", True, f"{path} names {', '.join(CI_CRED_KEYS)}")


def diagnose(repo_root: Path, *, scope: str = "full") -> list[tuple[str, bool, str]]:
    """Return a list of (check_name, ok, detail) tuples."""
    if scope not in {"full", "app"}:
        raise ValueError(f"unknown doctor scope: {scope}")
    results: list[tuple[str, bool, str]] = []

    for r in prereqs.run_all():
        results.append((r.name, r.ok, r.detail))

    # Before the instance-state early return: the off-box baseline nudge must
    # surface even on a box that was never (or incompletely) installed.
    required_registry_images = (
        {registry_push.REGISTRY_IMAGE} if scope == "app" else None
    )
    results.append(
        registry_push.check_registry_baseline(
            repo_root,
            required_registry_images=required_registry_images,
        )
    )

    state = load_instance(repo_root)
    if state is None:
        results.append(("instance state", False, "startup/.instance.json missing (was install ever run?)"))
        return results
    results.append(("instance state", True, f"name={state.name} prefix={state.prefix or '(none)'} ports={state.ports}"))
    results.append(check_ci_profile(state))
    results.append(check_ci_credentials())

    env = state.compose_env()
    health = (
        validate.run_app_health_checks
        if scope == "app"
        else validate.run_all_health_checks
    )
    for hr in health(state.ports, repo_root, env):
        results.append((hr.name, hr.ok, hr.detail))

    return results
