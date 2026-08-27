"""Fingerprinted judgment cache."""
from __future__ import annotations

import hashlib
import json

from nextseek_api.assistant.models_db import TurnJudgment

__all__ = [
    "fingerprint",
    "needs_judging",
    "record_failure",
    "record_judgment",
]


def fingerprint(row, *, prompt_version, model_id, schema_version):
    payload = json.dumps(
        {
            "session": row.session_id,
            "turn": row.turn_number,
            "route": row.route,
            "family": row.task_family,
            "prompt_version": prompt_version,
            "model_id": model_id,
            "schema_version": schema_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def needs_judging(rows, **versions):
    out = []
    for row in rows:
        fp = fingerprint(row, **versions)
        if not TurnJudgment.objects.filter(fingerprint=fp, status="ok").exists():
            out.append(row)
    return out


def _turn_pk(row):
    from nextseek_api.assistant.models_db import TurnLedger

    return TurnLedger.objects.get(session_id=row.session_id, turn_number=row.turn_number)


def record_judgment(row, *, verdict, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row),
        fingerprint=fingerprint(row, **versions),
        defaults={"verdict": verdict, "status": "ok", "error": None},
    )


def record_failure(row, *, error, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row),
        fingerprint=fingerprint(row, **versions),
        defaults={"verdict": None, "status": "failed", "error": error},
    )
