"""Healthcheck for the standalone `dispatch_attribute_outbox` process
(Section 6). Reads only the default-database heartbeat singleton and exits
nonzero when it is absent, stale, or was never observed."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from nextseek_api.attributes.models_async import AttributeOutboxDispatcherHeartbeat

HEARTBEAT_SINGLETON_KEY = "attribute_mutations"
MAX_AGE_SECONDS = 90


class Command(BaseCommand):
    help = "Exit nonzero unless the attribute-mutation outbox dispatcher heartbeat is fresh."

    def add_arguments(self, parser):
        parser.add_argument("--max-age-seconds", type=int, default=MAX_AGE_SECONDS)

    def handle(self, *args, **options):
        try:
            heartbeat = AttributeOutboxDispatcherHeartbeat.objects.get(singleton_key=HEARTBEAT_SINGLETON_KEY)
        except AttributeOutboxDispatcherHeartbeat.DoesNotExist:
            raise SystemExit("attribute outbox dispatcher heartbeat is absent")
        age = (timezone.now() - heartbeat.observed_at).total_seconds()
        if age > options["max_age_seconds"]:
            raise SystemExit(f"attribute outbox dispatcher heartbeat is stale ({age:.1f}s old)")
        if not heartbeat.owner:
            raise SystemExit("attribute outbox dispatcher heartbeat has no owner")
