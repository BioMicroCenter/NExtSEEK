from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0014_generation_activation_and_reservation"),
    ]

    operations = [
        migrations.AddField(
            model_name="turnledger",
            name="pinned_generation_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="turnledger",
            name="pinned_generation_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="GenerationActivationAudit",
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
                ("action", models.CharField(max_length=16)),
                ("previous_hash", models.CharField(blank=True, default="", max_length=64)),
                ("active_hash", models.CharField(max_length=64)),
                ("activated_by", models.CharField(max_length=128)),
                ("activated_at", models.DateTimeField(auto_now_add=True)),
                ("isolation_level", models.CharField(blank=True, default="", max_length=64)),
            ],
            options={
                "db_table": "eval_generation_activation_audit",
            },
        ),
    ]
