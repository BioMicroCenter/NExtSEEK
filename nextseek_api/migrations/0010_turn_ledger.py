"""TurnLedger with an idempotent MySQL charset/FK database heal.

The seeded live schema keeps ``assistant_chat_session.session_id`` in
latin1, while new tables inherit the database's utf8mb4 default.  A plain
CreateModel therefore leaves an empty, FK-less table when MySQL rejects the
deferred foreign key.  Keep Django's original state operations verbatim, but
converge the database through the same SeparateDatabaseAndState pattern used
by migrations 0007/0008.
"""
import django.db.models.deletion
from django.db import migrations, models

from ._turn_ledger_heal import heal, unheal


STATE_OPERATIONS = [
    migrations.CreateModel(
        name="TurnLedger",
        fields=[
            (
                "id",
                models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name="ID",
                ),
            ),
            ("turn_number", models.IntegerField()),
            ("route", models.CharField(max_length=64)),
            ("route_source", models.CharField(max_length=32)),
            (
                "task_family",
                models.CharField(blank=True, max_length=128, null=True),
            ),
            (
                "family_source",
                models.CharField(blank=True, max_length=32, null=True),
            ),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            (
                "session",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="turn_ledger",
                    to="nextseek_api.chatsession",
                ),
            ),
        ],
        options={"db_table": "assistant_turn_ledger"},
    ),
    migrations.AddIndex(
        model_name="turnledger",
        index=models.Index(
            fields=["task_family", "route"],
            name="assistant_t_task_fa_6d0f8a_idx",
        ),
    ),
    migrations.AddConstraint(
        model_name="turnledger",
        constraint=models.UniqueConstraint(
            fields=("session", "turn_number"), name="uniq_turn_per_session"
        ),
    ),
]


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("nextseek_api", "0009_normalize_chat_log_turn_ids")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=STATE_OPERATIONS,
            database_operations=[migrations.RunPython(heal, unheal)],
        )
    ]
