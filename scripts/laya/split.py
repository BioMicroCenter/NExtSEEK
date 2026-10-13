"""Held-out and calibration split helpers for the laya router (JevLevROUTING). Owner: unit U4.

Pure functions, no I/O except load_manifest. Signatures fixed by PLAN section 0.
"""
from __future__ import annotations

import hashlib
import json

HELDOUT_SALT = "jevlev-routing-heldout-v1"
CALIB_SALT = "jevlev-routing-calib-v1"
# ponytail: the 2026-10-03 draft chose families by a stratified greedy walk (DATA.md s12). A pure hash
# bucket cannot stratify, so these shares are tuned to land near the draft (13 families of ~40, 4 entities
# of ~25); route caps are checked by draft_heldout.py, not here.
FAMILY_PCT = 35
ENTITY_PCT = 15
CALIB_PCT = 15
_UNHELD = ("unlabelled", "none", "")


def _pct(salt: str, name: str) -> int:
    return int(hashlib.sha256((salt + name).encode()).hexdigest()[:8], 16) % 100


def heldout_bucket(family: str, entity: str) -> bool:
    """True when the whole family OR the whole primary entity is held out (sha256, salt jevlev-routing-heldout-v1)."""
    return (family not in _UNHELD and _pct(HELDOUT_SALT, family) < FAMILY_PCT) or (
        entity not in _UNHELD and _pct(HELDOUT_SALT, entity) < ENTITY_PCT)


def calib_bucket(chat_id: str) -> bool:
    """15% of chats (sha256, salt jevlev-routing-calib-v1)."""
    return _pct(CALIB_SALT, chat_id) < CALIB_PCT


def load_manifest(path) -> set[str]:
    """norm_text_hash values of the frozen held-out rows (manifest = one JSON row per line, no text)."""
    with open(path) as f:
        return {json.loads(line)["hash"] for line in f if line.strip()}
