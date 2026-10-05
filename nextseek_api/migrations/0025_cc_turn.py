"""The Container-CC turn pass (CCTurn) and the nested turn's link back to it.

``assistant_cc_turn.chat_id`` references ``assistant_chat_session.session_id``, which the seeded boxes keep in
latin1 while new tables take the utf8mb4 default, so a plain CreateModel would fail its foreign key there. The
table is created by ``_cc_turn_heal`` (SeparateDatabaseAndState, state verbatim), which matches the chat column to
its parent. ``parent_cc_turn`` references a bigint id, so it is a plain AddField. Non-atomic because the heal runs
DDL from RunPython (see nextseek_api/tests/test_runpython_ddl_atomicity.py).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from ._cc_turn_heal import heal, unheal

STATE_OPERATIONS = [
    migrations.CreateModel(
        name="CCTurn",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("pass_hash", models.CharField(db_index=True, max_length=64, unique=True)),
            ("login_nonce", models.BinaryField(blank=True, null=True)),
            ("login_ciphertext", models.BinaryField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("deadline_at", models.DateTimeField(blank=True, null=True)),
            ("expires_at", models.DateTimeField(blank=True, null=True)),
            ("revoked_at", models.DateTimeField(blank=True, null=True)),
            ("vocabulary", models.JSONField(blank=True, null=True)),
            ("plans", models.JSONField(default=dict)),
            ("strikes", models.JSONField(default=list)),
            ("ops_cost_usd", models.DecimalField(decimal_places=6, default=0, max_digits=10)),
            ("ops_in_flight", models.PositiveSmallIntegerField(default=0)),
            (
                "chat",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, to="nextseek_api.chatsession",
                ),
            ),
            (
                "task",
                models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="cc_turn",
                    to="nextseek_api.querytask",
                ),
            ),
            (
                "user",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL,
                ),
            ),
        ],
        options={
            "db_table": "assistant_cc_turn",
            "indexes": [models.Index(fields=["revoked_at", "expires_at"], name="assistant_cc_turn_live")],
        },
    ),
]


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nextseek_api", "0024_sampleshare"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=STATE_OPERATIONS,
            database_operations=[migrations.RunPython(heal, unheal)],
        ),
        migrations.AddField(
            model_name="querytask",
            name="parent_cc_turn",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="children",
                to="nextseek_api.ccturn",
            ),
        ),
    ]
