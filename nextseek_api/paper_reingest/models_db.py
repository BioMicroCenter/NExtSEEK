"""The paper reingest run (spec §4): one row, moving through fixed stages."""
import uuid

from django.conf import settings
from django.db import models


def new_run_id() -> str:
    return uuid.uuid4().hex


class PaperReingestRun(models.Model):
    STATES = [(s, s) for s in (
        "queued", "acquiring", "context", "curating", "verifying", "building", "ready",
        "applying", "applied", "paused", "failed", "superseded", "partially_applied")]
    MODES = [("new", "new"), ("update", "update")]

    run_id = models.CharField(max_length=32, unique=True, default=new_run_id, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paper_reingest_runs")
    chat_session_id = models.CharField(max_length=64, blank=True, default="")
    paper_input = models.CharField(max_length=1024)
    pmid = models.CharField(max_length=32, blank=True, default="")
    pmcid = models.CharField(max_length=32, blank=True, default="")
    doi = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=1024, blank=True, default="")
    mode = models.CharField(max_length=8, choices=MODES, default="new")
    project_hint = models.IntegerField(null=True, blank=True)
    state = models.CharField(max_length=24, choices=STATES, default="queued")
    failed_stage = models.CharField(max_length=24, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    artifacts = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="supersedes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paper_reingest_run"
        app_label = "nextseek_api"
        ordering = ["-created_at"]

    def __str__(self):
        return f"PaperReingestRun {self.run_id} ({self.state})"
