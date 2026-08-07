"""Continuously running transactional-outbox dispatcher (Section 6). A
request process never publishes directly -- every deployment that can
return 202 for an asynchronous mutation must run exactly this loop.

Note on discoverability: Django's per-app management-command scan only
walks each `INSTALLED_APPS` entry's own filesystem path
(`django.core.management.get_commands`); `nextseek_api.attributes` is a
plain subpackage of the `nextseek_api` app (its models use
`app_label="nextseek_api"`, not a separate `AppConfig`), so this module is
not itself on that scan path. A thin same-named shim under
`nextseek_api/management/commands/` (the directory Django actually scans,
already the home of `cc_sweep_staging.py`) re-exports this `Command` so
`manage.py dispatch_attribute_outbox` -- exactly what the compose service
below invokes -- resolves to this implementation.
"""
from __future__ import annotations

import os
import signal
import time

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from nextseek_api.attributes.jobs import ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE, ATTRIBUTE_MUTATION_OUTBOX_IDLE_SECONDS, dispatch_outbox, mutation_job_store
from nextseek_api.attributes.models_async import AttributeOutboxDispatcherHeartbeat
from nextseek_api.attributes.tasks import run_attribute_mutation

HEARTBEAT_SINGLETON_KEY = "attribute_mutations"


class Command(BaseCommand):
    help = "Continuously dispatch pending attribute-mutation outbox rows to the attribute_mutations queue."

    def add_arguments(self, parser):
        parser.add_argument("--iterations", type=int, default=None,
                             help="Run at most this many scan iterations, then exit (test/diagnostic use).")

    def handle(self, *args, **options):
        stopped = {"value": False}

        def stop(*_ignored):
            stopped["value"] = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        owner = f"{os.uname().nodename}:{os.getpid()}"
        delay = 0.25
        iterations = options.get("iterations")
        ran = 0
        while not stopped["value"]:
            heartbeat, _ = AttributeOutboxDispatcherHeartbeat.objects.get_or_create(
                singleton_key=HEARTBEAT_SINGLETON_KEY, defaults={"owner": owner},
            )
            changed = AttributeOutboxDispatcherHeartbeat.objects.filter(
                pk=heartbeat.pk, state_version=heartbeat.state_version,
            ).update(owner=owner, observed_at=timezone.now(), state_version=models.F("state_version") + 1)
            if changed != 1:
                raise RuntimeError("lost dispatcher heartbeat CAS")
            try:
                count = dispatch_outbox(
                    mutation_job_store(), run_attribute_mutation.apply_async,
                    limit=ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE, owner=owner,
                )
                delay = 0.25
            except Exception:  # noqa: BLE001 - bounded exponential backoff on database/broker errors
                ran += 1
                if iterations is not None and ran >= iterations:
                    return
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            ran += 1
            if iterations is not None and ran >= iterations:
                return
            if count == 0:
                time.sleep(ATTRIBUTE_MUTATION_OUTBOX_IDLE_SECONDS)
