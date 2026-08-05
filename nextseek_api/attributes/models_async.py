"""T08-owned heartbeat model for the standalone outbox dispatcher process
(Section 6: "the health command reads this row and exits nonzero when
absent/stale/wrong generation"). Lives on the default database, alongside
T03's `AttributeMutationJob`/`AttributeMutationPartition`.
"""
from __future__ import annotations

from django.db import models


class AttributeOutboxDispatcherHeartbeat(models.Model):
    singleton_key = models.CharField(max_length=64, unique=True)
    owner = models.CharField(max_length=255)
    observed_at = models.DateTimeField(auto_now=True, db_index=True)
    state_version = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "attributes_outbox_dispatcher_heartbeat"
        app_label = "nextseek_api"

    def __str__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AttributeOutboxDispatcherHeartbeat({self.singleton_key})"
