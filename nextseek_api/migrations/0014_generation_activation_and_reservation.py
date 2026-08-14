import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0013_family_posterior"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActiveGenerationPointer",
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
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                (
                    "activated_by",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "expected_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "active",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="nextseek_api.posteriorgeneration",
                    ),
                ),
                (
                    "previous",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="nextseek_api.posteriorgeneration",
                    ),
                ),
            ],
            options={
                "db_table": "eval_active_generation_pointer",
            },
        ),
        migrations.CreateModel(
            name="ApprovedRunManifest",
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
                ("manifest_hash", models.CharField(max_length=64, unique=True)),
                ("manifest", models.JSONField()),
                ("approved_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("max_spend_usd", models.DecimalField(decimal_places=6, max_digits=12)),
                ("max_calls", models.PositiveIntegerField()),
                ("consumed", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "eval_approved_run_manifest",
            },
        ),
        migrations.CreateModel(
            name="SpendReservation",
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
                ("attempt_id", models.CharField(max_length=64, unique=True)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                (
                    "reserved_usd",
                    models.DecimalField(decimal_places=6, max_digits=12),
                ),
                (
                    "actual_usd",
                    models.DecimalField(
                        blank=True, decimal_places=6, max_digits=12, null=True
                    ),
                ),
                ("status", models.CharField(default="pending", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reconciled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "manifest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reservations",
                        to="nextseek_api.approvedrunmanifest",
                    ),
                ),
            ],
            options={
                "db_table": "eval_spend_reservation",
            },
        ),
    ]
