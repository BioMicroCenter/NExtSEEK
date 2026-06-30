"""Configuration loading for the DMAC bridge."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_DEV_MODE_TRUE_VALUES = {"1", "true", "yes", "on"}

_DEV_DEFAULT_CLAUDE_USERS_ROOT = Path("~/dmac-dev/claude-users").expanduser()
_DEV_DEFAULT_SCRATCH_ROOT = Path("~/dmac-dev/scratch").expanduser()
_DEV_DEFAULT_DROPBOX_ROOT = Path("~/Library/CloudStorage/Dropbox/DMAC_Data").expanduser()
_DEV_DEFAULT_OUTPUT_ROOT = Path("~/dmac-dev/output").expanduser()
_DEFAULT_SIDECAR_NETWORK = "dmac-nextseek-net"
_DEV_DEFAULT_SIDECAR_STAGING_ROOT = Path("~/dmac-dev/nextseek-sidecar-staging").expanduser()
# OI-3 (T2): default URL the bridge hands the agent container for the Bedrock
# auth-proxy. R-9 parity: this MUST match bedrock-proxy/docker-compose.yml
# (service name `bedrock-proxy` + container port 8080, no host port). Override
# via DMAC_BEDROCK_PROXY_URL.
_DEFAULT_BEDROCK_PROXY_URL = "http://bedrock-proxy:8080"

# Repo root (…/dmac_assistant): .env is loaded from here regardless of process cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# B17c: dev-mode default catalog (vendored chat_nextseek 121-line catalog).
_DEV_DEFAULT_CATALOG_FILE = (
    _REPO_ROOT / "vendor" / "chat_nextseek" / "agent_model_catalog.json"
)


class ConfigError(ValueError):
    """Raised when bridge configuration cannot be loaded safely."""


class UserRecord(BaseModel):
    """One configured DMAC user."""

    model_config = ConfigDict(frozen=True)

    password: SecretStr
    projects: list[str] = Field(min_length=1)

    @field_validator("projects")
    @classmethod
    def _validate_projects(cls, value: list[str]) -> list[str]:
        if not all(isinstance(project, str) and project.strip() for project in value):
            raise ValueError("projects must contain at least one non-empty string")
        return value


class BridgeConfig(BaseModel):
    """Typed bridge configuration loaded from the process environment."""

    model_config = ConfigDict(frozen=True)

    users: dict[str, UserRecord]
    claude_users_root: Path
    scratch_root: Path
    dropbox_root: Path
    output_root: Path
    catalog_file: Path
    sidecar_network: str | None = None
    bedrock_proxy_url: str = _DEFAULT_BEDROCK_PROXY_URL
    sidecar_staging_root: Path | None = None
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8000

    @field_validator("catalog_file")
    @classmethod
    def _validate_catalog_file(cls, value: Path) -> Path:
        if not value.is_file():
            raise ValueError(f"catalog_file does not exist: {value}")
        # B17c D-NEW-7: parse-only JSON validation at bridge boot.
        # Catches malformed JSON before it reaches the container, where it
        # would surface as a chat_nextseek RuntimeError → agent debug → leak.
        # NO schema validation — chat_nextseek's _normalize_agent_model_catalog
        # remains the canonical shape gate.
        try:
            json.loads(value.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"catalog_file is not valid JSON: {value} ({exc})"
            ) from exc
        return value

    @field_validator("users")
    @classmethod
    def _validate_users(cls, value: dict[str, UserRecord]) -> dict[str, UserRecord]:
        if not value:
            raise ValueError("DMAC_USERS must define at least one user")
        return value

    @field_validator("bridge_host")
    @classmethod
    def _validate_bridge_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DMAC_BRIDGE_HOST must not be empty")
        return value

    @field_validator("bridge_port")
    @classmethod
    def _validate_bridge_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("DMAC_BRIDGE_PORT must be between 1 and 65535")
        return value


def _is_dev_mode() -> bool:
    return os.environ.get("DMAC_DEV_MODE", "").strip().lower() in _DEV_MODE_TRUE_VALUES


def _abs_root(path: Path) -> Path:
    """Make a config path absolute so it is a valid Docker bind source.

    ``~`` is expanded; a path that is still relative is resolved against the
    repo root (``_REPO_ROOT``) — deterministic and independent of the bridge
    process cwd — rather than the current working directory. Absolute paths
    pass through unchanged. Docker rejects relative bind sources (it reads them
    as named-volume names, e.g. ``var/scratch/demo`` -> "invalid characters for
    a local volume name"), so every directory root that becomes a mount must be
    absolute.
    """
    path = path.expanduser()
    if path.is_absolute():
        return path
    return (_REPO_ROOT / path).resolve()


def _required_path(name: str, default: Path | None = None) -> Path:
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return _abs_root(Path(raw))
    if default is not None and _is_dev_mode():
        return default
    raise ConfigError(f"{name} is required")


def _resolve_catalog_file() -> Path:
    """Resolve + validate DMAC_CATALOG_FILE_HOST_PATH at bridge boot (B17c).

    Existence + JSON-parseability checks run here so callers see a precise
    ConfigError message (D-NEW-7) instead of the generic "Bridge configuration
    is invalid" wrapper that pydantic ValidationError gets when raised from
    BridgeConfig._validate_catalog_file.
    """
    path = _required_path(
        "DMAC_CATALOG_FILE_HOST_PATH",
        default=_DEV_DEFAULT_CATALOG_FILE,
    )
    if not path.is_file():
        raise ConfigError(f"catalog_file does not exist: {path}")
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"catalog_file is not valid JSON: {path} ({exc})"
        ) from exc
    return path


def _load_users(raw_users: str) -> dict[str, UserRecord]:
    try:
        parsed = json.loads(raw_users)
    except json.JSONDecodeError as exc:
        raise ConfigError("DMAC_USERS contains invalid JSON") from exc

    if not isinstance(parsed, dict):
        raise ConfigError("DMAC_USERS must decode to an object")
    if not parsed:
        raise ConfigError("DMAC_USERS must define at least one user")

    for user_id in parsed:
        if not isinstance(user_id, str) or not _USER_ID_RE.fullmatch(user_id):
            raise ConfigError("DMAC_USERS contains an invalid user_id")

    try:
        return {
            user_id: UserRecord.model_validate(user_data)
            for user_id, user_data in parsed.items()
        }
    except ValidationError as exc:
        raise ConfigError("DMAC_USERS contains invalid user records") from exc


def load_config() -> BridgeConfig:
    """Load bridge configuration from the environment."""

    load_dotenv(_REPO_ROOT / ".env", override=False)
    raw_users = os.environ.get("DMAC_USERS")
    if raw_users is None:
        raise ConfigError("DMAC_USERS is required")

    try:
        return BridgeConfig(
            users=_load_users(raw_users),
            claude_users_root=_required_path(
                "DMAC_CLAUDE_USERS_ROOT",
                default=_DEV_DEFAULT_CLAUDE_USERS_ROOT,
            ),
            scratch_root=_required_path(
                "DMAC_SCRATCH_ROOT",
                default=_DEV_DEFAULT_SCRATCH_ROOT,
            ),
            dropbox_root=_required_path(
                "DMAC_DROPBOX_ROOT",
                default=_DEV_DEFAULT_DROPBOX_ROOT,
            ),
            output_root=_required_path(
                "DMAC_OUTPUT_ROOT",
                default=_DEV_DEFAULT_OUTPUT_ROOT,
            ),
            catalog_file=_resolve_catalog_file(),
            sidecar_network=os.environ.get("DMAC_SIDECAR_NETWORK", _DEFAULT_SIDECAR_NETWORK),
            bedrock_proxy_url=os.environ.get(
                "DMAC_BEDROCK_PROXY_URL", _DEFAULT_BEDROCK_PROXY_URL
            ),
            sidecar_staging_root=_required_path(
                "DMAC_SIDECAR_STAGING_ROOT",
                default=_DEV_DEFAULT_SIDECAR_STAGING_ROOT,
            ),
            bridge_host=os.environ.get("DMAC_BRIDGE_HOST", "127.0.0.1"),
            bridge_port=int(os.environ.get("DMAC_BRIDGE_PORT", "8000")),
        )
    except ValueError as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError("Bridge configuration is invalid") from exc
