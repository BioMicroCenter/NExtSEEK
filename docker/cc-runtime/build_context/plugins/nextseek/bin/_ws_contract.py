"""Canonical WS wire contract between the thin plugin client and the sidecar (OD-1, §12).

SINGLE SOURCE OF TRUTH at sidecar/app/contract.py (host-importable as sidecar.app.contract;
shipped in the sidecar image via `COPY sidecar/app/`). A byte-identical copy lives at
build_context/plugins/nextseek/bin/_ws_contract.py for the plugin bins (which import it
standalone, without the `sidecar` package); test_ws_contract_parity.py fails on drift.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SIDECAR_OPS = frozenset(
    {"entity", "parse", "api-read", "api-write", "graph", "report", "generate-submission"}
)

# §12 — fixed error code → CLI exit code. The thin client maps a sidecar error
# (or transport failure) to these exits so the plugin's observable taxonomy
# (recon:runner §1a) survives the refactor.
ERROR_EXIT: dict[str, int] = {
    "CONFIG_MISSING": 2,
    "IMPORT_FAILED": 2,
    "VALIDATION": 3,
    "AGENT_FAILED": 4,
    "WRITE_BLOCKED": 5,
    "CONFIG_ERROR": 6,
    "TRANSPORT_ERROR": 7,
    "AUTH_FAILED": 8,
    "STAGING_ERROR": 9,
}


class NsLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # min_length=1: an empty api_user would collapse all empty-login sessions onto
    # ns:{sha256("")} (one shared session/staging dir) — the sidecar must not rely on
    # client-side validation to keep identities distinct.
    api_user: str = Field(min_length=1)
    api_pass: str


class SidecarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    args: dict
    ns_login: NsLogin
    request_id: str

    @field_validator("op")
    @classmethod
    def _known_op(cls, v: str) -> str:
        if v not in SIDECAR_OPS:
            raise ValueError(f"unknown sidecar op: {v!r}")
        return v

    @field_validator("request_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        # Canonicalize: UUID(v) accepts urn:uuid:/brace/no-hyphen/uppercase spellings of
        # the same id, and request_id is used as a staging dir name — returning the raw
        # string would let one logical id fan out into distinct dirs. str(UUID(v)) yields
        # the canonical lowercase-hyphenated form. (Raises ValueError on a bad uuid.)
        return str(UUID(v))


class SidecarError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool = False

    @field_validator("code")
    @classmethod
    def _known_code(cls, v: str) -> str:
        if v not in ERROR_EXIT:
            raise ValueError(f"unknown error code: {v!r}")
        return v


class SidecarResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    status: str  # "ok" | "error"
    result: dict | None = None
    error: SidecarError | None = None


# Per-op arg schemas (strict). These mirror the runner's current argparse surface
# (recon:runner §1k) so the sidecar validates args the same way the CLI did.
class ApiWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)  # strict => no str→bool coercion
    parser_plan: str
    confirmed_write: bool = False
    query: str | None = None


class _QueryArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class _ApiReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parser_plan: str


class _ReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    project: str

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("samples", "protocols", "published", "rppr"):
            raise ValueError(f"bad report mode: {v!r}")
        return v


class _SubmissionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    uids: str
    query: str | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ("GEO", "SRA", "NFCORE_RNASEQ", "NFCORE_SCRNASEQ", "PRIDE"):
            raise ValueError(f"unsupported submission type: {v!r}")
        return v

    @field_validator("uids")
    @classmethod
    def _uids(cls, v: str) -> str:
        if not [u for u in v.split(",") if u.strip()]:
            raise ValueError("uids required (comma-separated)")
        return v


_OP_ARG_MODELS = {
    "entity": _QueryArg,
    "parse": _QueryArg,
    "graph": _QueryArg,
    "api-read": _ApiReadArgs,
    "api-write": ApiWriteArgs,
    "report": _ReportArgs,
    "generate-submission": _SubmissionArgs,
}


def validate_op_args(op: str, args: dict):
    """Validate args against the op's strict model. Raises ValidationError → the server
    maps to VALIDATION/exit 3 BEFORE dispatch (recon:runner §1a exit-3 parity)."""
    model = _OP_ARG_MODELS.get(op)
    if model is None:
        raise ValueError(f"no arg model for op {op!r}")
    return model(**args)
