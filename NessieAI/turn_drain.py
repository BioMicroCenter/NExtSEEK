"""Chat-turn markers for a graceful stop of the web container.

A chat turn runs on a daemon thread inside a gunicorn worker (four per container), so "which turns are running"
is a directory listing, not memory: ``start_turn_thread`` writes ``<dir>/<task_id>`` before the turn starts and
removes it when the target returns or raises. ``docker/scripts/entrypoint.sh web`` counts those files on a stop,
raises ``<dir>/.draining`` so the turn-start views refuse new turns, and ends the turns that outlast its cap
(``manage.py end_cut_turns``). The dir defaults to ``/tmp/nextseek-turns`` (``NEXTSEEK_TURN_DIR``).

Engine side, stdlib only, so ``NessieAI/cc/turn.py`` can start its turn here without importing the API side;
the 503 a draining view answers is ``nextseek_api.assistant.turn_drain.draining_response``.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
from pathlib import Path


logger = logging.getLogger(__name__)

DRAINING_FLAG = ".draining"
DRAINING_DETAIL = "NExtSEEK is restarting. Send your question again in a minute."
CUT_MESSAGE = "NExtSEEK restarted for an update while answering, so this answer stopped. Please ask again."


def turn_dir() -> Path:
    return Path(os.environ.get("NEXTSEEK_TURN_DIR", "/tmp/nextseek-turns"))


def draining() -> bool:
    return (turn_dir() / DRAINING_FLAG).exists()


def start_turn_thread(task_id, target, *, thread_cls=None, **kwargs):
    """Run ``target(**kwargs)`` on a daemon thread, with a marker file for as long as it runs.

    ``thread_cls`` is the seam the turn tests use to run a turn inline."""
    marker = turn_dir() / str(task_id)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        # A turn must still run when the dir is unwritable; it just is not counted by a drain.
        logger.warning("could not write the turn marker %s", marker, exc_info=True)

    @functools.wraps(target)
    def _marked(*args, **kw):
        try:
            return target(*args, **kw)
        finally:
            marker.unlink(missing_ok=True)

    thread = (thread_cls or threading.Thread)(target=_marked, **({"kwargs": kwargs} if kwargs else {}), daemon=True)
    thread.start()
    return thread
