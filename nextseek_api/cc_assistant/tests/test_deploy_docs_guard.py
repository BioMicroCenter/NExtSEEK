"""Doc guards for the deployment docs (PLAN-7 Task 8 Step 1).

Keeps ``nextseek_api/cc_assistant/DEPLOY.md`` and the repo-root
``DEPLOYMENT.md`` compose-native forever: the retired manual bootstrap
(standalone dmac-assistant repo build, hand-run docker network
create/connect, host-path prep under the legacy /srv tree, token harvesting
from running containers) must never reappear as a required step, and the
numbered procedure must keep ``./startup.sh install`` — the compose file
declares external volumes that nothing else creates.

Deliberately hermetic: stdlib + file reads only (no Django, no docker), so it
runs identically in every lane. Text is normalized (shell ``\\``-newline
continuations joined) before matching, so a forbidden command split across
continuation lines — the docs' prevailing command style — cannot evade the
guard. Guard patterns intentionally do NOT key on the bare token "sidecar" —
the compose-owned ``nextseek-sidecar`` service is legitimate required
topology (PLAN-7 Task 8 / G7-11 iter-1 M-3).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_MD = REPO_ROOT / "nextseek_api" / "cc_assistant" / "DEPLOY.md"
DEPLOYMENT_MD = REPO_ROOT / "DEPLOYMENT.md"

# Patterns that must not appear ANYWHERE in the deploy docs (not just the
# numbered steps — the "appendix dodge" is also a failure). Matched against
# continuation-normalized text (see _normalize).
FORBIDDEN = {
    "manual network create": re.compile(r"docker\s+network\s+create"),
    "manual network connect": re.compile(r"docker\s+network\s+connect"),
    "standalone proxy repo build": re.compile(r"make\s+proxy-build"),
    "standalone agent repo build (make)": re.compile(r"make\s+image-build"),
    "standalone agent repo build (buildx)": re.compile(r"buildx\s+build[^\n]*dmac-assistant"),
    "standalone agent repo build (docker build)": re.compile(r"docker\s+build[^\n]*dmac-assistant"),
    "legacy phase marker": re.compile(r"\bPhases?\s+[AB]\b"),
    "token harvested from a running container": re.compile(
        r"docker\s+exec[^\n]*\b(printenv|env|echo)\b[^\n]*AWS_BEARER_TOKEN_BEDROCK"
    ),
    "env values dumped without name-only filter": re.compile(
        r"docker\s+inspect[^\n]*Config\.Env(?![^\n]*cut\s+-d=)"
    ),
}

_LEGACY_HOST_ROOT = "/srv/dmac/users"
_HOST_PREP = re.compile(r"\b(mkdir|chmod)\b")

# Shell line continuations: backslash-newline (plus indentation) joins the
# logical command back onto one line before pattern matching.
_CONTINUATION = re.compile(r"\\\s*\n\s*")


def _normalize(text: str) -> str:
    return _CONTINUATION.sub(" ", text)


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} is missing — the deploy docs were moved or deleted"
    return _normalize(path.read_text(encoding="utf-8"))


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


@pytest.mark.parametrize(
    "evasion",
    [
        # continuation-split network surgery / standalone builds
        "docker network \\\n  create dmac-cc-net",
        "docker network \\\n    connect --alias nextseek_nginx dmac-cc-net x",
        "docker buildx build \\\n  --platform=linux/amd64 -t dmac-assistant:poc .",
        "docker build \\\n  -t dmac-assistant:poc ~/dmac-assistant",
        "cd ~/dmac-assistant && make image-build",
        "make proxy-build",
        # non-heading phase markers
        "**Phase A: build the proxy**",
        "the old Phases A/B bootstrap",
        # token-harvest variants
        "TOK=$(docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK)",
        "docker exec dmac-bedrock-proxy env | grep AWS_BEARER_TOKEN_BEDROCK",
        "docker exec nextseek sh -c 'echo $AWS_BEARER_TOKEN_BEDROCK'",
        "docker inspect dmac-bedrock-proxy --format '{{.Config.Env}}'",
    ],
    ids=lambda s: s.replace("\n", "␤")[:44],
)
def test_forbidden_patterns_catch_known_evasions(evasion):
    """Regression lock for the demonstrated guard evasions: each retired-
    bootstrap form must be caught on normalized text."""
    text = _normalize(evasion)
    caught = any(p.search(text) for p in FORBIDDEN.values())
    assert caught, f"evasion not caught by any FORBIDDEN pattern: {evasion!r}"


def test_guard_does_not_flag_legitimate_content():
    """The compose-owned nextseek-sidecar service and the name-only env
    enumeration are required/legitimate content; the forbidden patterns must
    never match them (M-3)."""
    legitimate = [
        "the nextseek-sidecar service is started by docker compose up -d and "
        "mounts a subpath of the dmac-cc-users volume",
        "docker compose build cc-agent refreshes the dmac-assistant:poc image",
        "docker inspect nextseek-sidecar --format "
        "'{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1",
        "install runs 9 phases: prereq checks, config render, volumes, seeds",
        "docker exec nextseek printenv NEXTSEEK_SERVER",
    ]
    for sample in legitimate:
        text = _normalize(sample)
        hits = [name for name, p in FORBIDDEN.items() if p.search(text)]
        assert not hits, f"false positive {hits} on legitimate text: {sample!r}"
        assert not (_LEGACY_HOST_ROOT in text and _HOST_PREP.search(text))


def test_docs_point_at_each_other():
    """DEPLOY.md defers full-stack hygiene to DEPLOYMENT.md; DEPLOYMENT.md
    routes CC specifics to DEPLOY.md — the pointer pair must survive edits."""
    assert "DEPLOYMENT.md" in _read(DEPLOY_MD)
    assert "nextseek_api/cc_assistant/DEPLOY.md" in _read(DEPLOYMENT_MD)
