"""The graph_sync state tables (docs/superpowers/specs/2026-09-15-graph-search-sync-design.md, section 12).

``GraphSyncOutbox`` holds the work waiting for ``manage.py graph_sync --loop``: scheduled slots now, and rows written
by per-writer hooks once they land. ``GraphSyncRun`` records every run of ``--full``, ``--catalog``, ``--reconcile``,
``--drift`` and ``--samples``. Both live in the dmac (``default``) database; the rules that use them go in
``state.py``.

Re-exported from ``nextseek_api/models.py``, which is what makes the app registry load them.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone


class GraphSyncOutbox(models.Model):
    """One unit of work: ``kind`` says what to do, ``key`` what to do it to. ``(kind, key)`` is unique, so a scheduled
    slot is inserted once and repeated hook writes coalesce into one row."""

    kind = models.CharField(max_length=32)
    key = models.CharField(max_length=191)
    payload = models.JSONField(null=True, blank=True)
    enqueued_at = models.DateTimeField(default=timezone.now)
    claimed_by = models.CharField(max_length=255, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "graph_sync_outbox"
        app_label = "nextseek_api"
        constraints = [models.UniqueConstraint(fields=["kind", "key"], name="graph_sync_outbox_kind_key")]
        indexes = [models.Index(fields=["done_at", "enqueued_at"], name="graph_sync_outbox_due")]

    def __str__(self) -> str:
        return f"GraphSyncOutbox({self.kind} {self.key})"


class GraphSyncRun(models.Model):
    """One run. ``status`` is ``running`` until the run ends, then ``ok``, ``failed``, ``refused`` or ``abandoned``;
    a drift run ends ``ok`` when every check passed and ``drift`` when one failed."""

    kind = models.CharField(max_length=32)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="running")
    watermark_from = models.CharField(max_length=64, null=True, blank=True)
    watermark_to = models.CharField(max_length=64, null=True, blank=True)
    counts_json = models.JSONField(null=True, blank=True)
    drift_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "graph_sync_run"
        app_label = "nextseek_api"
        indexes = [models.Index(fields=["kind", "status", "finished_at"], name="graph_sync_run_kind_status")]

    def __str__(self) -> str:
        return f"GraphSyncRun({self.kind} {self.status})"
