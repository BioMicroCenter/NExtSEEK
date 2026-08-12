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


class TurnLedger(models.Model):
    """Durable per-turn identity for evaluation export and judgment cache."""

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="turn_ledger"
    )
    turn_number = models.IntegerField()
    route = models.CharField(max_length=64)
    route_source = models.CharField(max_length=32)
    task_family = models.CharField(max_length=128, null=True, blank=True)
    family_source = models.CharField(max_length=32, null=True, blank=True)
    pinned_generation_id = models.BigIntegerField(null=True, blank=True)
    pinned_generation_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assistant_turn_ledger"
        app_label = "nextseek_api"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "turn_number"], name="uniq_turn_per_session"
            )
        ]
        indexes = [models.Index(fields=["task_family", "route"])]


class TurnJudgment(models.Model):
    """Fingerprinted judge verdict for one ledger turn."""

    turn = models.ForeignKey(
        TurnLedger, on_delete=models.CASCADE, related_name="judgments"
    )
    fingerprint = models.CharField(max_length=64, db_index=True)
    verdict = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16)  # ok | failed
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "eval_turn_judgment"
        app_label = "nextseek_api"
        constraints = [
            models.UniqueConstraint(
                fields=["turn", "fingerprint"], name="uniq_turn_fingerprint"
            )
        ]


class PosteriorGeneration(models.Model):
    """Immutable published posterior generation (V4-5 store)."""

    generation_hash = models.CharField(max_length=64, unique=True)
    input_hash = models.CharField(max_length=64)
    config_fingerprint = models.CharField(max_length=64)
    decision_status = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "eval_posterior_generation"
        app_label = "nextseek_api"


class FamilyPosterior(models.Model):
    """Per-family route posterior row bound to one generation."""

    generation = models.ForeignKey(
        PosteriorGeneration,
        on_delete=models.CASCADE,
        related_name="posteriors",
    )
    task_family = models.CharField(max_length=128)
    route = models.CharField(max_length=64)
    posterior_mean = models.FloatField()
    band = models.CharField(max_length=32)
    n_total = models.IntegerField()
    fitted_at = models.DateTimeField()

    class Meta:
        db_table = "eval_family_posterior"
        app_label = "nextseek_api"
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "task_family", "route"],
                name="uniq_generation_family_route",
            )
        ]


class ActiveGenerationPointer(models.Model):
    """Singleton active-generation pointer with CAS activation audit."""

    active = models.ForeignKey(
        PosteriorGeneration,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    previous = models.ForeignKey(
        PosteriorGeneration,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.CharField(max_length=128, blank=True, default="")
    expected_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "eval_active_generation_pointer"
        app_label = "nextseek_api"


class GenerationActivationAudit(models.Model):
    """Append-only activation/rollback audit trail (V4-5)."""

    action = models.CharField(max_length=16)
    previous_hash = models.CharField(max_length=64, blank=True, default="")
    active_hash = models.CharField(max_length=64)
    activated_by = models.CharField(max_length=128)
    activated_at = models.DateTimeField(auto_now_add=True)
    isolation_level = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "eval_generation_activation_audit"
        app_label = "nextseek_api"


class ApprovedRunManifest(models.Model):
    """Immutable approved run manifest for V4-8 provider authorization."""

    manifest_hash = models.CharField(max_length=64, unique=True)
    manifest = models.JSONField()
    approved_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    max_spend_usd = models.DecimalField(max_digits=12, decimal_places=6)
    max_calls = models.PositiveIntegerField()
    consumed = models.BooleanField(default=False)

    class Meta:
        db_table = "eval_approved_run_manifest"
        app_label = "nextseek_api"


class SpendReservation(models.Model):
    """Atomic pre-call budget reservation against an approved manifest."""

    STATUS_PENDING = "pending"
    STATUS_RECONCILED = "reconciled"
    STATUS_RELEASED = "released"
    STATUS_EXPIRED = "expired"

    manifest = models.ForeignKey(
        ApprovedRunManifest,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    attempt_id = models.CharField(max_length=64, unique=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    reserved_usd = models.DecimalField(max_digits=12, decimal_places=6)
    actual_usd = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "eval_spend_reservation"
        app_label = "nextseek_api"


class CCSessionTranscript(models.Model):
    """Full Claude Code session jsonl, zstd-compressed, per (session, turn).

    Stored in its OWN table (NOT ChatSession.extra_state) so it is loaded only on
    demand and never bloats hot ChatSession reads (SPEC-3 §7, E6)."""

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
