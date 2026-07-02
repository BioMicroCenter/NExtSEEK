"""``manage.py cc_sweep_staging`` — trusted-code recovery / gate entrypoint for
the NS sidecar staging sweep (G7-11, Task 14).

This is the SAME callable trusted-code entrypoint that ``cc_engine.run_cc_turn``
invokes in-turn (``nextseek_api.cc_assistant.cc_staging.sweep_user_staging``),
exposed here for:

* recovery of OLDER completed strays left by a crashed/timed-out earlier turn,
  and
* the Task 15 capability gate, whose 9 ops cannot run inside a live 180 s turn:
  it invokes this identical code path ONCE, immediately after its op matrix
  completes, with the invocation recorded in evidence.

Runs in the ``nextseek`` container as the trusted Django process:

    docker exec nextseek python manage.py cc_sweep_staging \\
        --user-id <django_user> --api-user <ns_login> --project <pid>-<slug>

Sweeps ALL completed ``.complete`` request dirs for the given user (recovery
mode, ``since_ts=None``): staged artifacts are moved into that user's own
``{project}/{user}/scratch/nextseek-artifacts/`` subtree. Emits a JSON summary
(delivered relpaths + counts) to stdout so the gate can capture it as evidence.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from nextseek_api.cc_assistant import cc_staging
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


class Command(BaseCommand):
    help = (
        "Sweep completed NS sidecar staging artifacts into a user's own CC "
        "scratch subtree (G7-11 recovery / Task 15 gate entrypoint)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--user-id", required=True, help="Django username (scratch subpath segment).")
        parser.add_argument("--api-user", required=True, help="NExtSEEK login the sidecar hashed the staging dir by.")
        parser.add_argument("--project", required=True, help="Validated project dirname ({pid}-{slug}).")

    def handle(self, *args, **options) -> None:
        user_id = options["user_id"]
        api_user = options["api_user"]
        project = options["project"]

        paths = CCPaths.from_env()
        # scratch_mnt is session-independent — recovery does not need a session.
        try:
            dirs = build_user_dirs(paths, project, user_id)
        except ValueError as exc:
            raise CommandError(f"invalid identity: {exc}") from exc

        try:
            result = cc_staging.sweep_user_staging(
                user_root_mount=paths.user_root_mount,
                scratch_dir=dirs.scratch_mnt,
                api_user=api_user,
                user_id=user_id,
                project_dirname=project,
                since_ts=None,  # recovery: sweep ALL completed request dirs
            )
        except ValueError as exc:
            raise CommandError(f"invalid identity: {exc}") from exc

        self.stdout.write(
            json.dumps(
                {
                    "user_id": user_id,
                    "project": project,
                    "delivered": sorted(result.delivered),
                    "delivered_count": len(result.delivered),
                    "scratch_dir": dirs.scratch_mnt,
                },
                separators=(",", ":"),
            )
        )
