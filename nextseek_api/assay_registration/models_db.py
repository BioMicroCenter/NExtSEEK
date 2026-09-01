"""Durable record of one batch assay-registration request.

Modelled on attributes/models_db.py's AttributeMutationJob, but deliberately
simpler: that job carries per-sample-type partitions with their own CAS state,
and a registration batch has no partitions. What is kept is the discipline that
matters, namely optimistic concurrency on `state_version` so two workers cannot
both believe they own the job.
"""
from __future__ import annotations

import uuid

from django.db import models


class AssayRegistrationJob(models.Model):
    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False,
                              db_index=True)

    actor_django_user_id = models.BigIntegerField()
    actor_login = models.CharField(max_length=255)

    submitted_request = models.JSONField(default=dict)

    state = models.CharField(max_length=32, default="accepted")
    state_version = models.PositiveBigIntegerField(default=0)

    claim_owner = models.CharField(max_length=255, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    processed_rows = models.PositiveIntegerField(default=0)
    total_rows = models.PositiveIntegerField(default=0)

    terminal_result = models.JSONField(null=True, blank=True)

    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancellation_actor_django_user_id = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    ACTIVE_STATES = ("accepted", "queued", "running")

    class Meta:
        app_label = "nextseek_api"
        indexes = [models.Index(fields=["state", "created_at"])]

    def __str__(self) -> str:
        return f"AssayRegistrationJob({self.job_id}, {self.state})"
