"""The paper reingest run (spec §4): one row, moving through fixed stages."""
import hashlib
import uuid

from django.conf import settings
from django.db import models

#: The unfinished states: while a run of a paper is in one, a second start of that paper is refused.
LIVE_STATES = ("queued", "acquiring", "context", "curating", "verifying", "building", "ready",
               "paused", "applying", "partially_applied")


def new_run_id() -> str:
    return uuid.uuid4().hex


def paper_key(doi: str | None, paper_input: str) -> str:
    """A paper's identity among unfinished runs: its DOI (case folded), else the input as given. Hashed, so it
    fits a unique index on MySQL whatever the input's length."""
    doi = (doi or "").strip().lower()
    identity = f"doi:{doi}" if doi else f"input:{(paper_input or '').strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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
    #: ``paper_key`` while the run is unfinished, NULL once it is not. Unique, so the database itself refuses a
    #: second unfinished run of one paper, whoever starts it; NULLs never collide (MySQL and SQLite alike).
    live_key = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paper_reingest_run"
        app_label = "nextseek_api"
        ordering = ["-created_at"]

    def __str__(self):
        return f"PaperReingestRun {self.run_id} ({self.state})"

    def paper_key(self) -> str:
        return paper_key(self.doi, self.paper_input)

    def save(self, *args, **kwargs):
        # The key follows state and superseded_by. A save that writes neither leaves the stored key alone, so an
        # old copy of the row saving some other field cannot give a run finished elsewhere its key back.
        update_fields = kwargs.get("update_fields")
        if update_fields is None or {"state", "superseded_by"} & set(update_fields):
            live = self.state in LIVE_STATES and self.superseded_by_id is None
            self.live_key = self.paper_key() if live else None
            if update_fields is not None and "live_key" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "live_key"]
        super().save(*args, **kwargs)
