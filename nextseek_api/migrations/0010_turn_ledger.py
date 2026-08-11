import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0009_normalize_chat_log_turn_ids"),
    ]

    operations = [
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
            options={
                "db_table": "assistant_turn_ledger",
            },
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
