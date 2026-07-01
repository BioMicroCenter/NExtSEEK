import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0006_merge_extra_state_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="CCSessionTranscript",
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
                ("cc_session_id", models.CharField(max_length=128)),
                ("turn_id", models.CharField(max_length=128)),
                ("blob", models.BinaryField()),
                ("uncompressed_size", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chat_session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cc_transcripts",
                        to="nextseek_api.chatsession",
                    ),
                ),
            ],
            options={
                "db_table": "assistant_cc_transcript",
                "ordering": ["-created_at"],
                "unique_together": {("chat_session", "cc_session_id", "turn_id")},
            },
        ),
    ]
