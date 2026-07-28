"""Doc guards for the deployment docs (PLAN-7 Task 8 Step 1).

Keeps ``nextseek_api/cc_assistant/DEPLOY.md`` and the repo-root
``DEPLOYMENT.md`` compose-native forever: the retired manual bootstrap
(standalone dmac-assistant repo build, hand-run docker network
create/connect, host-path prep under the legacy /srv tree) must never
reappear as a required step, and the numbered procedure must keep
``./startup.sh install`` — the compose file declares external volumes that
nothing else creates.

Deliberately hermetic: stdlib + file reads only (no Django, no docker), so it
runs identically in every lane. Guard patterns intentionally do NOT key on
the bare token "sidecar" — the compose-owned ``nextseek-sidecar`` service is
legitimate required topology (PLAN-7 Task 8 / G7-11 iter-1 M-3).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_MD = REPO_ROOT / "nextseek_api" / "cc_assistant" / "DEPLOY.md"
DEPLOYMENT_MD = REPO_ROOT / "DEPLOYMENT.md"

# Patterns that must not appear ANYWHERE in the deploy docs (not just the
# numbered steps — the "appendix dodge" is also a failure).
FORBIDDEN = {
    "manual network create": re.compile(r"docker\s+network\s+create"),
    "manual network connect": re.compile(r"docker\s+network\s+connect"),
    "standalone proxy repo build": re.compile(r"make\s+proxy-build"),
    "standalone agent repo build": re.compile(r"buildx\s+build[^\n]*dmac-assistant"),
    "legacy phase-marker heading": re.compile(r"^#{1,6}\s.*\bPhase\s+[AB]\b", re.MULTILINE),
    "token copied out of a running container": re.compile(
        r"docker\s+exec[^\n]*printenv[^\n]*AWS_BEARER_TOKEN_BEDROCK"
    ),
}

_LEGACY_HOST_ROOT = "/srv/dmac/users"
_HOST_PREP = re.compile(r"\b(mkdir|chmod)\b")


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} is missing — the deploy docs were moved or deleted"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", [DEPLOY_MD, DEPLOYMENT_MD], ids=lambda p: p.name)
def test_no_retired_bootstrap_anywhere(doc):
    text = _read(doc)
    hits = {name: pat.search(text) for name, pat in FORBIDDEN.items() if pat.search(text)}
    assert not hits, (
        f"{doc.name} reintroduces retired manual-bootstrap step(s): "
        + ", ".join(f"{name} ({m.group(0)!r})" for name, m in hits.items())
    )


@pytest.mark.parametrize("doc", [DEPLOY_MD, DEPLOYMENT_MD], ids=lambda p: p.name)
def test_no_legacy_host_path_prep(doc):
    text = _read(doc)
    if _LEGACY_HOST_ROOT in text:
        assert not _HOST_PREP.search(text), (
            f"{doc.name} mentions {_LEGACY_HOST_ROOT} alongside mkdir/chmod — "
            "host-path prep under the legacy tree is retired (SPEC-7 §7/G7-10: "
            "the CC user tree is the external dmac-cc-users volume)"
        )


def test_deploy_md_procedure_requires_startup_install():
    """docker-compose.yml declares external volumes; only ./startup.sh install
    creates them, so the numbered procedure must include it."""
    text = _read(DEPLOY_MD)
    match = re.search(r"^##\s+Procedure\s*$(.*?)(?=^##\s)", text, re.MULTILINE | re.DOTALL)
    assert match, "DEPLOY.md must keep a '## Procedure' section with numbered steps"
    assert "./startup.sh install" in match.group(1), (
        "DEPLOY.md's numbered procedure must include './startup.sh install' — "
        "compose expects external volumes that nothing else creates"
    )


def test_deployment_md_requires_startup_install():
    assert "./startup.sh install" in _read(DEPLOYMENT_MD)


def test_guard_does_not_flag_legitimate_sidecar_service():
    """The compose-owned nextseek-sidecar service is required topology; the
    forbidden patterns must never match plain mentions of it (M-3)."""
    legitimate = (
        "the nextseek-sidecar service is started by docker compose up -d and "
        "mounts a subpath of the dmac-cc-users volume"
    )
    for name, pat in FORBIDDEN.items():
        assert not pat.search(legitimate), f"pattern {name!r} false-positives on sidecar text"
    assert not (_LEGACY_HOST_ROOT in legitimate and _HOST_PREP.search(legitimate))


def test_docs_point_at_each_other():
    """DEPLOY.md defers full-stack hygiene to DEPLOYMENT.md; DEPLOYMENT.md
    routes CC specifics to DEPLOY.md — the pointer pair must survive edits."""
    assert "DEPLOYMENT.md" in _read(DEPLOY_MD)
    assert "nextseek_api/cc_assistant/DEPLOY.md" in _read(DEPLOYMENT_MD)
