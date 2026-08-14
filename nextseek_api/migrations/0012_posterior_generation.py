import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0011_turn_judgment"),
    ]

    operations = [
        migrations.CreateModel(
            name="PosteriorGeneration",
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
                ("generation_hash", models.CharField(max_length=64, unique=True)),
                ("input_hash", models.CharField(max_length=64)),
                ("config_fingerprint", models.CharField(max_length=64)),
                ("decision_status", models.CharField(max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children",
                        to="nextseek_api.posteriorgeneration",
                    ),
                ),
            ],
            options={
                "db_table": "eval_posterior_generation",
            },
        ),
    ]
