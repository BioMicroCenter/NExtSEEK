"""Shared pure helpers for the laya router (JevLevROUTING). Owner: unit U3.

Signatures fixed by PLAN section 0.
No torch, no laya import in this file (SPEC test 13).
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from .followup import followup_rule_text
from .router_context import HistoryTurn

QUERY_CAP = 1200
PREV_CAP = 300
_REPO = pathlib.Path(__file__).resolve().parents[2]


def _one_line(text: str, cap: int) -> str:
    return " ".join((text or "").split())[:cap]


def condense(query: str, history: list[HistoryTurn]) -> str:
    """SPEC s4 exact format, deterministic. No reply text, errors, uids or counts."""
    lines = ["Current message: " + _one_line(query, QUERY_CAP)]
    if history:
        last = history[-1]
        lines.append(f"Previous message ({last.router_choice or 'none'}, {last.status}): "
                     + _one_line(last.user_message, PREV_CAP))
    if len(history) > 1:
        lines.append("Earlier routes in this chat: "
                     + ", ".join(t.router_choice or "none" for t in history[:-1]))
    return "\n".join(lines)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def options_hash(options: dict) -> str:
    """sha256 of the rendered option texts (SPEC s5): keys and texts in order, nothing else."""
    rendered = [[o["key"], o["text"]] for o in options["options"]]
    return _sha(json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


_PROMPT_FILES = ("NessieAI/dmac_assistant/baml_src/router.baml",
                 "NessieAI/dmac_assistant/build_context/route_capabilities.json",
                 "NessieAI/dmac_assistant/baml_src/clients.baml")


def prompt_hash(root: pathlib.Path | None = None) -> str:
    """sha256 over the router prompt files and the follow-up rule text for the current mode.

    The rule text is the one the router renders (split or cc), so a mode change is a prompt change.
    """
    root = pathlib.Path(root) if root else _REPO
    h = hashlib.sha256()
    for rel in _PROMPT_FILES:
        h.update(rel.encode() + b"\0" + _sha((root / rel).read_bytes()).encode() + b"\0")
    h.update(b"followup\0" + followup_rule_text().encode("utf-8"))
    return h.hexdigest()


def apply_temperature(probs: dict[str, float], T: float) -> dict[str, float]:
    """p_i^(1/T), renormalised (SPEC s8 Calibration)."""
    pw = {k: v ** (1.0 / T) for k, v in probs.items()}
    tot = sum(pw.values())
    return {k: v / tot for k, v in pw.items()}


def norm_text_hash(text: str) -> str:
    """Lowercase, collapse whitespace, sha256 hex."""
    return _sha(" ".join((text or "").lower().split()).encode("utf-8"))
