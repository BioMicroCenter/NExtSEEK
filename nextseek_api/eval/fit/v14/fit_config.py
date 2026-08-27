"""Fingerprinted V14 fit and decision configuration."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["V14FitConfig", "contrast_basis_B", "config_fingerprint"]


def contrast_basis_B() -> np.ndarray:
    """Orthonormal contrast basis for 4 sum-to-zero logits (3D)."""
    # Simplex contrast: reference = both_fail; columns are orthonormal in R^4 restricted to sum=0
    b = np.array(
        [
            [1.0, 0.0, 0.0, -1.0],
            [0.0, 1.0, 0.0, -1.0],
            [0.0, 0.0, 1.0, -1.0],
        ],
        dtype=float,
    ).T  # 4 x 3
    q, _ = np.linalg.qr(b)
    return q[:, :3]


class V14FitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "v14-fit/v1"
    quality_prior_scale: float = 2.0
    latency_prior_scale: float = 1.0
    latency_nu: float = 4.0
    latency_sigma_prior_scale: float = 1.0
    quality_advantage_threshold: float = 0.10
    posterior_probability_threshold: float = 0.95
    rope_half_width: float = 0.10
    latency_ratio_threshold: float = 0.80
    reporting_slowdown_warning: float = 0.25
    fdr_threshold: float = 0.05
    min_retained_pairs: int = 5
    min_discordant_pairs: int = 2
    rhat_max: float = 1.01
    ess_min: float = 400.0
    num_warmup: int = 600
    num_samples: int = 2000
    num_chains: int = 2


def config_fingerprint(cfg: V14FitConfig) -> str:
    payload = cfg.model_dump()
    payload["contrast_basis_sha256"] = hashlib.sha256(contrast_basis_B().tobytes()).hexdigest()
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
