"""Companion heal for deployments where 0007 is already recorded (Bug C).

Django never re-runs a recorded migration, so the rewritten 0007 is inert on
any box whose ledger already lists it — notably the live dev DB, where 0007
was recorded out-of-band on 2026-07-01 while its FK ALTER had failed (child
column utf8mb4 vs latin1 parent, FK absent). This DB-only migration runs the
SAME idempotent heal: it aligns ``chat_session_id`` to the parent's
charset/collation and adds the missing FK there, and no-ops everywhere the
rewritten 0007 (or Django's native path) already converged the schema.
"""
from django.db import migrations

from ._cc_transcript_heal import heal


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("nextseek_api", "0007_ccsessiontranscript"),
    ]

    operations = [
        migrations.RunPython(heal, migrations.RunPython.noop),
    ]
