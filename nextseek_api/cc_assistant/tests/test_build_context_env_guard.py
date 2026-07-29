"""Build-context hygiene: rendered env files (and any ad-hoc copies of them)
must never enter the docker build context.

Motivation: .dockerignore used to exclude only the three EXACT filenames
(docker/db.env, docker/nextseek.env, docker/bedrock-proxy/proxy-secret.env).
Operator backup copies like ``docker/nextseek.env.bak.<date>`` matched nothing
and were swept into the image by ``COPY . /app``. These tests evaluate the
actual .dockerignore rules (last-match-wins, ``**`` and ``!`` semantics) against
representative paths, so the protection is functional, not textual.
Hermetic: stdlib only, no git, no docker.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
GITIGNORE = REPO_ROOT / ".gitignore"


def _pattern_to_regex(pattern: str) -> re.Pattern:
    # Minimal .dockerignore glob semantics: '**/' spans any number of
    # directories (including zero), '*' and '?' stop at '/'. A trailing '/'
    # (directory pattern) also matches everything beneath it.
    pat = pattern.strip().rstrip("/")
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif pat[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "(/.*)?$")


def _rules():
    rules = []
    for raw in DOCKERIGNORE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        rules.append((negate, _pattern_to_regex(line[1:] if negate else line)))
    assert rules, ".dockerignore parsed to zero rules"
    return rules


def _excluded(path: str) -> bool:
    verdict = False
    for negate, rx in _rules():
        if rx.match(path):
            verdict = not negate
    return verdict


MUST_BE_EXCLUDED = [
    # the three canonical rendered secret files
    "docker/db.env",
    "docker/nextseek.env",
    "docker/bedrock-proxy/proxy-secret.env",
    "dmac/local_settings.py",
    # the ad-hoc operator copies that actually leaked into an image (2026-07-29)
    "docker/nextseek.env.bak-pre-seekpublicurl",
    "docker/nextseek.env.bak.20260727123629",
    "docker/nextseek.env.bak.budget-20260729113017",
    "docker/db.env.bak",
    "docker/db.env.old",
    # root compose-interpolation env and any nested real .env
    ".env",
    "chat_nextseek/.env",
    # bak copies at arbitrary depth / with suffixes
    "some/deep/dir/foo.env.bak2",
    "nextseek.env.bak.rootlevel",
]

MUST_BE_INCLUDED = [
    # committed templates and examples stay in the build context
    "docker/nextseek.env.example",
    "docker/bedrock-proxy/proxy-secret.env.example",
    "startup/templates/nextseek.env.template",
    "startup/templates/db.env.template",
    "chat_frontend/.env.example",
    "chat_nextseek/.env.example",
    # sanity: ordinary source stays included
    "README.md",
    "dmac/settings.py",
]


@pytest.mark.parametrize("path", MUST_BE_EXCLUDED)
def test_secretish_paths_excluded_from_build_context(path):
    assert _excluded(path), f"{path} would enter the docker build context"


@pytest.mark.parametrize("path", MUST_BE_INCLUDED)
def test_templates_and_source_stay_included(path):
    assert not _excluded(path), f"{path} is wrongly excluded from the build context"


def test_gitignore_covers_env_bak_copies():
    lines = [
        ln.strip()
        for ln in GITIGNORE.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for required in (
        "*.env.bak*",
        ".env",
        "docker/nextseek.env",
        "docker/db.env",
        "docker/bedrock-proxy/proxy-secret.env",
    ):
        assert required in lines, f".gitignore lost the `{required}` rule"
