import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0010_turn_ledger"),
    ]

    operations = [
        migrations.CreateModel(
            name="TurnJudgment",
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
                ("fingerprint", models.CharField(db_index=True, max_length=64)),
                ("verdict", models.JSONField(blank=True, null=True)),
                ("status", models.CharField(max_length=16)),
                ("error", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "turn",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="judgments",
                        to="nextseek_api.turnledger",
                    ),
                ),
            ],
            options={
                "db_table": "eval_turn_judgment",
            },
        ),
        migrations.AddConstraint(
            model_name="turnjudgment",
            constraint=models.UniqueConstraint(
                fields=("turn", "fingerprint"), name="uniq_turn_fingerprint"
            ),
        ),
    ]
