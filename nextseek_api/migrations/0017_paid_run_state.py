from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0016_paired_run_registry"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaidRunState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.CharField(db_index=True, max_length=128)),
                ("overlap_lock", models.CharField(max_length=128, unique=True)),
                ("arm_id", models.CharField(max_length=64)),
                ("attempt_id", models.CharField(max_length=64)),
                ("status", models.CharField(default="pending", max_length=16)),
                ("cache_key", models.CharField(blank=True, default="", max_length=256)),
                ("failure_reason", models.TextField(blank=True, default="")),
                ("backoff_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manifest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="paid_run_states",
                        to="nextseek_api.approvedrunmanifest",
                    ),
                ),
            ],
            options={
                "db_table": "eval_paid_run_state",
            },
        ),
        migrations.AddConstraint(
            model_name="paidrunstate",
            constraint=models.UniqueConstraint(
                fields=("run_id", "arm_id", "attempt_id"),
                name="uniq_paid_run_arm_attempt",
            ),
        ),
    ]
