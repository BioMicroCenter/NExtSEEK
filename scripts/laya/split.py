"""Held-out and calibration split helpers for the laya router (JevLevROUTING). Owner: unit U4.

Stage 0 skeleton: signatures fixed by PLAN section 0; bodies land in U4.
"""
from __future__ import annotations


def heldout_bucket(family: str, entity: str) -> bool:
    """sha256 bucket with salt "jevlev-routing-heldout-v1"."""
    raise NotImplementedError


def calib_bucket(chat_id: str) -> bool:
    """15% bucket, salt "jevlev-routing-calib-v1"."""
    raise NotImplementedError


def load_manifest(path) -> set[str]:
    """norm_text_hash values of the frozen held-out rows."""
    raise NotImplementedError
