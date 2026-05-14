"""Read-only diagnostic for an existing install."""
from __future__ import annotations

from pathlib import Path

from bootstrap.lib.instance import load_instance
from bootstrap.steps import prereqs, validate


def diagnose(repo_root: Path) -> list[tuple[str, bool, str]]:
    """Return a list of (check_name, ok, detail) tuples."""
    results: list[tuple[str, bool, str]] = []

    for r in prereqs.run_all():
        results.append((r.name, r.ok, r.detail))

    state = load_instance(repo_root)
    if state is None:
        results.append(("instance state", False, "bootstrap/.instance.json missing (was install ever run?)"))
        return results
    results.append(("instance state", True, f"name={state.name} prefix={state.prefix or '(none)'} ports={state.ports}"))

    env = state.compose_env()
    for hr in validate.run_all_health_checks(state.ports, repo_root, env):
        results.append((hr.name, hr.ok, hr.detail))

    return results
