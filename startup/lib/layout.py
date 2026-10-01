"""Repo-relative locations the startup CLI shares across its steps.

startup/ is its own uv project and never imports NessieAI/ (nor ci/), so these
are restated here rather than imported from NessieAI/paths.py. Each one is
pinned against the file that actually consumes it by
startup/tests/test_layout.py, so a later move fails a test instead of a box.
"""
from __future__ import annotations

from pathlib import Path

# The bedrock-proxy env_file (docker-compose.yml, service bedrock-proxy). It
# holds the institutional Bedrock token, so install's render, reset's removal
# list, doctor's token check and the operator messages all read this one
# value. A stale copy is worse than a crash: write_env() creates missing
# parents, so it would quietly write the token into a directory nothing reads.
PROXY_SECRET_ENV = Path("NessieAI") / "docker" / "bedrock-proxy" / "proxy-secret.env"

# Where the token lived before the NessieAI move. Never written: install reads
# it only as a fallback so a re-run on a box that has not moved the file yet
# keeps its token, and reset removes it along with the rest of the config.
LEGACY_PROXY_SECRET_ENV = Path("docker") / "bedrock-proxy" / "proxy-secret.env"

# The NS engine unit (dist and import name chat_nextseek). Install checks that
# it is present before it writes anything.
CHAT_NEXTSEEK_DIR = Path("NessieAI") / "chat_nextseek"

# The context files the cc-agent image bakes from the checkout, and where they
# land in it (CANONICAL_CONTEXT_FILES and IMAGE_CONTEXT_DIR in
# NessieAI/build_tools/gen_op_surfaces/constants.py). The app image carries the
# same files, so an edit here needs both `./startup.sh rebuild` and
# `./startup.sh rebuild --component cc-agent`; the stack-health check
# `cc-agent context` is what notices when the second one was skipped.
CANONICAL_CONTEXT_DIR = CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context"
CC_AGENT_CONTEXT_DIR = "/app/plugins/nextseek/context"
CANONICAL_CONTEXT_FILES = (
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_sampletypes_db.json",
    "projects_db.json",
)


def proxy_secret_env(repo_root: Path) -> Path:
    """The proxy token file for the checkout at ``repo_root``."""
    return repo_root / PROXY_SECRET_ENV


def legacy_proxy_secret_env(repo_root: Path) -> Path:
    """The pre-move proxy token file for the checkout at ``repo_root``."""
    return repo_root / LEGACY_PROXY_SECRET_ENV
