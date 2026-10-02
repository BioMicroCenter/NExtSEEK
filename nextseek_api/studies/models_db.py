"""The share mode's job row (tool spec 16.4, 16.5; T38): one row per share request, its state and the plan's facts.

The discipline of ``nextseek_api/assay_registration/models_db.py`` and ``jobs.py`` (a UUID job id, compare-and-set on
``state_version``, an owner-scoped lease), copied rather than shared: a registration job validates every stored
request as a ``RegistrationRequest`` and its status ``state`` is a closed Literal of another public endpoint.
"""
from __future__ import annotations

import uuid

from django.db import models


class SampleShare(models.Model):
    share_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    actor_django_user_id = models.BigIntegerField()
    actor_login = models.CharField(max_length=255)
    request = models.JSONField(default=dict)            # the ShareInput, UIDs included
    state = models.CharField(max_length=32, default="planning")
    state_version = models.PositiveBigIntegerField(default=0)
    claim_owner = models.CharField(max_length=255, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    run_dir = models.CharField(max_length=512, blank=True, default="")
    plan_sha256 = models.CharField(max_length=64, blank=True, default="")
    summary = models.JSONField(null=True, blank=True)
    receipt = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STATES = ("planning", "planned", "plan_failed", "refused", "applying", "queued", "running", "applied",
              "apply_failed", "rolled_back")
    CLAIMABLE = ("planning", "queued")

    class Meta:
        app_label = "nextseek_api"
        indexes = [models.Index(fields=["state", "created_at"])]

    def __str__(self) -> str:
        return f"SampleShare({self.share_id}, {self.state})"
