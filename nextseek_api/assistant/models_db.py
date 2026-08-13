import uuid

from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    results_history = models.JSONField(default=list)
    last_debug = models.JSONField(default=dict)
    extra_state = models.JSONField(default=dict)
    title = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assistant_chat_session"
        app_label = 'nextseek_api'
        ordering = ["-created_at"]

    def __str__(self):
        return f"ChatSession {self.session_id} (user={self.user_id})"


class QueryTask(models.Model):
    """Tracks an async query pipeline execution.

    Created by POST /assistant/query/async/.  Progress events are
    appended to ``progress`` as the pipeline runs.  The WebSocket
    consumer and polling endpoint both read from this model.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("error", "Error"),
    ]

    task_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="query_tasks",
    )
    query = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    progress = models.JSONField(default=list)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assistant_query_task"
        app_label = 'nextseek_api'
        ordering = ["-created_at"]

    def __str__(self):
        return f"QueryTask {self.task_id} ({self.status})"


class CCSessionTranscript(models.Model):
    """One CC turn's Claude Code transcript jsonl, zstd-compressed, per (session, turn).

    Stored in its OWN table (NOT ChatSession.extra_state) so it is loaded only on
    demand and never bloats hot ChatSession reads (SPEC-3 §7, E6).

    A row is written for every CC invocation THAT APPENDED RECORDS OF ITS OWN,
    including turns that FAILED — a ``query_error``, a watchdog timeout, or an
    exception out of ``run_cc_turn`` (#68). It used to be written only from the
    ``query_complete`` branch, which left exactly the turns worth triaging as
    the only ones with no durable record.

    Two failure shapes still persist NOTHING, deliberately (``run_cc_turn``'s
    ``finally``), so a missing row is not impossible and the turn-addressed
    recover endpoint still 404s: a turn whose agent produced no transcript at
    all (a spawn that died before the agent ran), and a turn whose store gained
    no records — there the only bytes available are EARLIER turns' and filing
    them under this ``turn_id`` would misattribute another turn's transcript.
    Both log at WARNING. Do not "fix" either into writing a row.

    ``blob`` holds only THAT turn's records, not the cumulative ``--resume``
    session file: Claude Code appends every turn of a chat to one session jsonl,
    so storing all of it per turn made row N hold turns 1..N and the stored bytes
    grow quadratically in turn count. A reader wanting the whole conversation
    folds the rows in ``created_at`` order (``nessie_tests/sources.py``'s
    ``merge_transcripts`` does it by containment, correct for either shape)."""

    chat_session = models.ForeignKey(
        "nextseek_api.ChatSession", on_delete=models.CASCADE,
        related_name="cc_transcripts",
    )
    cc_session_id = models.CharField(max_length=128)
    turn_id = models.CharField(max_length=128)
    blob = models.BinaryField()
    uncompressed_size = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assistant_cc_transcript"
        app_label = "nextseek_api"
        unique_together = (("chat_session", "cc_session_id", "turn_id"),)
        ordering = ["-created_at"]
