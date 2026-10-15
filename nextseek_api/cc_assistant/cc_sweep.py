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

The beat runs every 300 s. ``NEXTSEEK_CC_SUMMARY_SWEEP=0`` (or false/off) stops the sweep; at most
``DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN`` (20) sessions are summarized per run, oldest first.
"""
from __future__ import annotations

import logging
import os

from NessieAI.cc import cc_memory

logger = logging.getLogger(__name__)


def select_sweep_targets(metas, now_ts: float, idle_seconds: int):
    """Sessions idle >= idle_seconds AND changed since their last summary."""
    return [m for m in metas
            if m.changed and (now_ts - m.updated_at) >= idle_seconds]


def sweep_enabled() -> bool:
    """Kill switch: ``NEXTSEEK_CC_SUMMARY_SWEEP`` = 0/false/off stops the paid sweep (default on)."""
    return os.environ.get("NEXTSEEK_CC_SUMMARY_SWEEP", "1").strip().lower() not in ("0", "false", "off")


def max_per_run() -> int:
    """``DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN`` (default 20): most sessions summarized in one run."""
    try:
        return max(0, int(os.environ.get("DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN", "20")))
    except ValueError:
        return 20


def _run_sweep():
    """Django/Celery body — imported lazily so module import stays hermetic."""
    from django.contrib.auth.models import User
    from django.utils import timezone
    from NessieAI.cc import cc_summary, cc_config, cc_engine, cc_session
    from NessieAI.router import router as cc_router
    from NessieAI.cc.turn import _session_metas, _persist_summary_standalone

    paths = cc_config.CCPaths.from_env()
    mem_cfg = cc_config.CCMemoryConfig.from_env()
    now_ts = timezone.now().timestamp()
    count = 0
    skipped_unverified = 0
    targets = []
    for user in User.objects.all():
        metas = _session_metas(user, current_id=None, paths=paths, mem_cfg=mem_cfg)
        targets += [(user, t) for t in select_sweep_targets(metas, now_ts, mem_cfg.sweep_idle_seconds)
                    if t.transcript_path]
    # Oldest first, at most the cap of paid summaries per run (unverified skips cost nothing, so they never
    # use up the cap): bounds the spend if many chats go idle at once.
    targets.sort(key=lambda ut: ut[1].updated_at)
    cap = max_per_run()
    for n, (user, tgt) in enumerate(targets):
        if count >= cap:
            logger.info("cc-1c sweep: cap %d reached, %d session(s) wait for the next run", cap, len(targets) - n)
            break
        try:
            # G7-10: transcript_path is the mount path inside the dmac-cc-users
            # volume. Read from its store down, never through a link (the store
            # is an agent's folder).
            raw = cc_session.read_store_transcript(tgt.transcript_path)
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
            summary = cc_summary.summarize_transcript(
                raw, prov, mem_cfg, summarize_fn=cc_summary.sweep_summarize_fn)
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
        if not sweep_enabled():
            logger.info("cc-1c sweep disabled (NEXTSEEK_CC_SUMMARY_SWEEP)")
            return 0
        return _run_sweep()
except Exception:  # pragma: no cover - celery absent in hermetic env
    logger.info("cc-1c: celery unavailable; sweep task not registered")
