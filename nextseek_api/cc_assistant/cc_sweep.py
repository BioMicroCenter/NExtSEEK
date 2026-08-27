"""Step 1c Celery backstop: summarize idle, changed sessions the user never
'left' via a new chat, and retry fallback/failed ones. Idempotent (unchanged
fingerprint -> skipped). The pure selector is Django/Celery-free for hermetic test.

#76: this task runs on a beat with no request in scope, so it holds no user
credential and CANNOT scrub the transcript it is about to send to a third-party
summarizer — the scrubbing happened, or did not, in the ``finally`` of the turn
that wrote it, on a daemon thread a worker recycle or SIGKILL skips outright.
So every transcript is gated on ``cc_engine.transcript_is_verified_scrubbed``
before it is read for summarization, and an unverified one is SKIPPED.

A skipped session stays ``changed``, so it is re-selected and warns again on
every beat (300 s) until a CC turn by its owner scrubs and watermarks it. That
repetition is the intended operator signal — a session sitting unverified is
one holding bytes nobody has cleaned — not log noise to suppress.
"""
from __future__ import annotations

import logging

from nextseek_api.cc_assistant import cc_memory

logger = logging.getLogger(__name__)


def select_sweep_targets(metas, now_ts: float, idle_seconds: int):
    """Sessions idle >= idle_seconds AND changed since their last summary."""
    return [m for m in metas
            if m.changed and (now_ts - m.updated_at) >= idle_seconds]


def _run_sweep():
    """Django/Celery body — imported lazily so module import stays hermetic."""
    from pathlib import Path
    from django.contrib.auth.models import User
    from django.utils import timezone
    from nextseek_api.cc_assistant import cc_summary, cc_config, cc_engine, router as cc_router
    from nextseek_api.services.cc_assistant import _session_metas, _persist_summary_standalone

    paths = cc_config.CCPaths.from_env()
    mem_cfg = cc_config.CCMemoryConfig.from_env()
    now_ts = timezone.now().timestamp()
    count = 0
    skipped_unverified = 0
    for user in User.objects.all():
        metas = _session_metas(user, current_id=None, paths=paths, mem_cfg=mem_cfg)
        for tgt in select_sweep_targets(metas, now_ts, mem_cfg.sweep_idle_seconds):
            if not tgt.transcript_path:
                continue
            try:
                # G7-10: transcript_path is already the mount path inside the
                # dmac-cc-users volume — read it directly (no host translation).
                raw = Path(tgt.transcript_path).read_bytes()
                # #76: fail closed. These bytes go to a third-party summarizer
                # and from there into the merged CLAUDE.md mounted into later
                # agent containers. This task has no credential to scrub them
                # with, so it may only forward bytes some turn already proved
                # clean. No watermark -> the turn that wrote this file never
                # reached its scrub (worker recycle / SIGKILL kills the daemon
                # thread), or the agent appended after it did.
                if not cc_engine.transcript_is_verified_scrubbed(
                        tgt.transcript_path, raw):
                    skipped_unverified += 1
                    logger.warning(
                        "cc #76 sweep: %s carries no scrub watermark for its "
                        "current contents — NOT summarizing (it may hold "
                        "unredacted credentials); a CC turn by its owner will "
                        "scrub and re-admit it",
                        tgt.session_id)
                    continue
                prov = cc_summary.SummaryProvenance(
                    chat_session_id=tgt.session_id,
                    claude_session_id=(tgt.summary or {}).get("claude_session_id"),
                    transcript_path=tgt.transcript_path,
                    chat_model=cc_router._resolve_cc_model_id() or "",
                    generated_at=timezone.now().isoformat())
                summary = cc_summary.summarize_transcript(raw, prov, mem_cfg)
                _persist_summary_standalone(user, tgt.session_id, summary.model_dump(),
                                            cc_summary.fingerprint(raw))
                count += 1
            except Exception:
                logger.exception("cc-1c sweep: failed for %s", tgt.session_id)
    logger.info("cc-1c sweep: summarized %d session(s), skipped %d unverified (#76)",
                count, skipped_unverified)
    return count


try:
    from celery import shared_task

    @shared_task(name="cc_assistant.sweep_cc_summaries")
    def sweep_cc_summaries():
        return _run_sweep()
except Exception:  # pragma: no cover - celery absent in hermetic env
    logger.info("cc-1c: celery unavailable; sweep task not registered")
