from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0015_v4_5_generation_audit_and_turn_pin"),
    ]

    operations = [
        migrations.CreateModel(
            name="PairedRunRegistry",
            fields=[
                (
                    "paired_run_id",
                    models.CharField(max_length=128, primary_key=True, serialize=False),
                ),
                ("schema_version", models.CharField(max_length=32)),
                ("content_hash", models.CharField(max_length=64)),
                ("approved_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "eval_paired_run_registry",
            },
        ),
    ]
