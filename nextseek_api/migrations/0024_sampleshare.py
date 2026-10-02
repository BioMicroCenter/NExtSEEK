"""The studies tool's share jobs (nextseek_api/studies/models_db.py SampleShare): one new table, nothing else."""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0023_graph_sync_outbox_failing_since"),
    ]

    operations = [
        migrations.CreateModel(
            name="SampleShare",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("share_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("actor_django_user_id", models.BigIntegerField()),
                ("actor_login", models.CharField(max_length=255)),
                ("request", models.JSONField(default=dict)),
                ("state", models.CharField(default="planning", max_length=32)),
                ("state_version", models.PositiveBigIntegerField(default=0)),
                ("claim_owner", models.CharField(blank=True, max_length=255, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("run_dir", models.CharField(blank=True, default="", max_length=512)),
                ("plan_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("summary", models.JSONField(blank=True, null=True)),
                ("receipt", models.JSONField(blank=True, null=True)),
                ("error", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [models.Index(fields=["state", "created_at"], name="nextseek_ap_state_111ff9_idx")],
            },
        ),
    ]
