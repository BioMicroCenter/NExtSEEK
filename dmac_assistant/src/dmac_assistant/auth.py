"""Authentication and token issuance for the DMAC bridge."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, SecretStr

from dmac_assistant.config import BridgeConfig, ConfigError, UserRecord, load_config


class AuthenticationError(ValueError):
    """Raised when login or token verification fails."""


class AuthenticatedIdentity(BaseModel):
    """The authenticated user identity bound to a session token."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    password: SecretStr
    projects: list[str]


class IssuedToken(BaseModel):
    """Opaque bearer token returned by the login route."""

    model_config = ConfigDict(frozen=True)

    token: str
    expires_at: datetime


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    user_id: str
    password: str


class _StoredToken(BaseModel):
    """In-memory token binding."""

    model_config = ConfigDict(frozen=True)

    identity: AuthenticatedIdentity
    expires_at: datetime


class TokenStore:
    """In-memory token store with fixed TTL."""

    def __init__(self, *, ttl: timedelta = timedelta(hours=12)) -> None:
        self._ttl = ttl
        self._tokens: dict[str, _StoredToken] = {}

    def issue(
        self,
        *,
        user_id: str,
        password: str,
        config: BridgeConfig,
        now: datetime | None = None,
    ) -> IssuedToken:
        user_record = _lookup_user(config=config, user_id=user_id)
        if user_record.password.get_secret_value() != password:
            raise AuthenticationError("invalid credentials")

        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self._ttl
        token = secrets.token_urlsafe(32)
        identity = AuthenticatedIdentity(
            user_id=user_id,
            password=SecretStr(password),
            projects=list(user_record.projects),
        )
        self._tokens[token] = _StoredToken(identity=identity, expires_at=expires_at)
        return IssuedToken(token=token, expires_at=expires_at)

    def verify(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedIdentity:
        stored = self._tokens.get(token)
        if stored is None:
            raise AuthenticationError("invalid token")

        current_time = now or datetime.now(UTC)
        if current_time >= stored.expires_at:
            self._tokens.pop(token, None)
            raise AuthenticationError("expired token")

        return stored.identity

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


router = APIRouter(prefix="/auth", tags=["auth"])
_TOKEN_STORE = TokenStore()


def get_config() -> BridgeConfig:
    """Dependency wrapper around config loading."""
    try:
        return load_config()
    except ConfigError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"bridge misconfigured: {err}",
        ) from err


def get_token_store() -> TokenStore:
    """Dependency wrapper around the process-local token store."""
    return _TOKEN_STORE


def _lookup_user(*, config: BridgeConfig, user_id: str) -> UserRecord:
    user = config.users.get(user_id)
    if user is None:
        raise AuthenticationError("invalid credentials")
    return user


@router.post("/login", response_model=IssuedToken)
def login(
    payload: LoginRequest,
    config: BridgeConfig = Depends(get_config),
    token_store: TokenStore = Depends(get_token_store),
) -> IssuedToken:
    """Issue a new bearer token for a configured user."""
    try:
        return token_store.issue(
            user_id=payload.user_id,
            password=payload.password,
            config=config,
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        ) from exc


def verify_token_dependency(
    authorization: Annotated[str | None, Header()] = None,
    token_store: TokenStore = Depends(get_token_store),
) -> AuthenticatedIdentity:
    """FastAPI dependency for Bearer token verification."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return token_store.verify(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
