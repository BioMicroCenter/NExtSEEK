"""Read-only diagnostic for an existing install."""
from __future__ import annotations

from pathlib import Path

from startup.lib.instance import load_instance
from startup.steps import prereqs, registry_push, validate


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

    env = state.compose_env()
    health = (
        validate.run_app_health_checks
        if scope == "app"
        else validate.run_all_health_checks
    )
    for hr in health(state.ports, repo_root, env):
        results.append((hr.name, hr.ok, hr.detail))

    return results
