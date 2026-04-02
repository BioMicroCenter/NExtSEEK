"""Configuration and tunables for batch upload pipeline."""
from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, ConfigDict

log = logging.getLogger(__name__)


class BatchUploadConfig:
    """All tunables for the batch upload pipeline.

    Defaults can be overridden via environment variables where noted.
    """

    def __init__(self, **overrides):
        self.max_rows_per_batch: int = int(
            os.environ.get("BATCH_UPLOAD_MAX_ROWS", overrides.get("max_rows_per_batch", 2000))
        )
        self.min_batch_size: int = overrides.get("min_batch_size", 500)
        self.max_batch_size: int = overrides.get("max_batch_size", 5000)
        self.max_payload_bytes: int = overrides.get("max_payload_bytes", 8_388_608)  # 8 MB
        self.target_rows_per_sec: float = overrides.get("target_rows_per_sec", 100.0)
        self.adaptive_batching: bool = overrides.get("adaptive_batching", True)
        self.parallel_threshold: int = overrides.get("parallel_threshold", 5000)
        self.gc_every_n_batches: int = overrides.get("gc_every_n_batches", 10)
        self.clear_caches_every_n_batches: int = overrides.get("clear_caches_every_n_batches", 20)
        self.mem_limit_mb: Optional[int] = _env_int(
            "BATCH_UPLOAD_MEM_LIMIT_MB", overrides.get("mem_limit_mb")
        )
        self.enable_auto_permissions: bool = _env_bool(
            "ENABLE_AUTO_PERMISSIONS", overrides.get("enable_auto_permissions", True)
        )
        self.permissions_default_access_type: int = int(
            os.environ.get("PERMISSIONS_DEFAULT_ACCESS_TYPE",
                           overrides.get("permissions_default_access_type", 4))
        )
        self.permissions_default_contributor_type: str = os.environ.get(
            "PERMISSIONS_DEFAULT_CONTRIBUTOR_TYPE",
            overrides.get("permissions_default_contributor_type", "Project"),
        )
        self.assay_cache_max: int = int(
            os.environ.get("ASSAY_CACHE_MAX", overrides.get("assay_cache_max", 10_000))
        )
        self.title_to_id_cache_max: int = int(
            os.environ.get("TITLE_TO_ID_CACHE_MAX", overrides.get("title_to_id_cache_max", 5000))
        )
        # Optional row limit for EXTRACT stage (None = no limit)
        self.limit: Optional[int] = overrides.get("limit", None)
        self.update_existing: bool = _env_bool(
            "BATCH_UPLOAD_UPDATE_EXISTING", overrides.get("update_existing", False)
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class Neo4jConfig(BaseModel):
    """Neo4j connection configuration loaded from Django settings."""

    NEO4J_DB: str = ""
    URI: str = ""
    NEO4J_USER: str = ""
    PASSWORD: str = ""
    NEO4J_UPLOAD_ENABLED: bool = False
    NEO4J_NODE_CHUNK: int = int(os.environ.get("NEO4J_NODE_CHUNK", 10_000))
    NEO4J_REL_CHUNK: int = int(os.environ.get("NEO4J_REL_CHUNK", 20_000))
    NEO4J_DEBUG: bool = False
    DRIVER_AVAILABLE: bool = False
    MISSING_KEYS: List[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    @classmethod
    @lru_cache(maxsize=1)
    def from_django_settings(cls) -> "Neo4jConfig":
        """Build Neo4jConfig from Django settings.NEO4J_DATABASE."""
        from django.conf import settings

        neo4j_conf = getattr(settings, "NEO4J_DATABASE", {})
        missing: List[str] = []
        db_name = neo4j_conf.get("NAME", "")
        uri = neo4j_conf.get("URI", "")
        auth: Tuple[str, str] = neo4j_conf.get("AUTH", ("", ""))
        user = auth[0] if len(auth) > 0 else ""
        password = auth[1] if len(auth) > 1 else ""

        if not db_name:
            missing.append("NAME")
        if not uri:
            missing.append("URI")
        if not user:
            missing.append("AUTH[0] (user)")
        if not password:
            missing.append("AUTH[1] (password)")

        driver_available = False
        try:
            import neo4j as _neo4j  # noqa: F401
            driver_available = True
        except ImportError:
            missing.append("neo4j driver not installed")

        enabled = len(missing) == 0

        cfg = cls(
            NEO4J_DB=db_name,
            URI=uri,
            NEO4J_USER=user,
            PASSWORD=password,
            NEO4J_UPLOAD_ENABLED=enabled,
            DRIVER_AVAILABLE=driver_available,
            MISSING_KEYS=missing,
        )
        if not enabled:
            log.warning("Neo4j upload disabled — missing: %s", ", ".join(missing))
        return cfg


def get_sqlalchemy_url() -> str:
    """Build a SQLAlchemy connection URL from Django DATABASES['seek']."""
    from django.conf import settings

    db = settings.DATABASES["seek"]
    host = db.get("HOST") or "127.0.0.1"
    port = db.get("PORT") or "3306"
    user = db.get("USER", "")
    password = db.get("PASSWORD", "")
    name = db.get("NAME", "")
    return f"mysql+mysqldb://{user}:{password}@{host}:{port}/{name}"


# ── helpers ────────────────────────────────────────────────────────────────


def _env_int(key: str, default=None) -> Optional[int]:
    val = os.environ.get(key)
    if val is not None:
        return int(val)
    return int(default) if default is not None else None


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes")
    return bool(default)
