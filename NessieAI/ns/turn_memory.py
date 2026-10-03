"""A Container-CC turn's shared state in Django (approach 1). This half (plan 03): the op slots.

At most ``MAX_OPS_IN_FLIGHT`` ops of one turn run at once (operator ruling 2026-09-28): the web server runs few
workers and an api-read op calls back into Django, so more could stall the site. ``CCTurn.ops_in_flight`` counts them.
A slot is taken with one conditional UPDATE (never read, then written), so two ops on two workers cannot both take the
last one, and it is given back in ``run_op``'s ``finally``. Only Django writes the row.

A worker killed outright (SIGKILL, the OOM killer) runs no ``finally``: its slot stays taken until the turn ends,
which halves that one turn's op capacity and touches no other turn.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_OPS_IN_FLIGHT = 2


def take_op_slot(turn: Any) -> bool:
    """Take one of the turn's op slots; False when ``MAX_OPS_IN_FLIGHT`` are already taken."""
    from django.db.models import F

    from nextseek_api.assistant.models_db import CCTurn

    taken = CCTurn.objects.filter(pk=turn.pk, ops_in_flight__lt=MAX_OPS_IN_FLIGHT).update(
        ops_in_flight=F("ops_in_flight") + 1)
    return taken == 1


def release_op_slot(turn: Any) -> None:
    """Give back a slot ``take_op_slot`` took. Never below zero. When the worker's connection dropped during a long op,
    one retry on a fresh connection. Never raises: it runs in a ``finally`` and must not hide the op's own error."""
    from django.db import connection
    from django.db.models import F

    from nextseek_api.assistant.models_db import CCTurn

    for attempt in (1, 2):
        try:
            CCTurn.objects.filter(pk=turn.pk, ops_in_flight__gt=0).update(ops_in_flight=F("ops_in_flight") - 1)
            return
        except Exception:  # InterfaceError is not a DatabaseError; nothing may leave the caller's finally
            if attempt == 2:
                logger.warning("cc turn %s: an op slot could not be given back", turn.pk, exc_info=True)
                return
            try:
                connection.close()
            except Exception:
                pass
