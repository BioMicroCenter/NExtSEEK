"""CCSessionTranscript, rewritten in place as SeparateDatabaseAndState (Bug C).

The original plain CreateModel failed on every seeded greenfield: the deferred
``ADD FOREIGN KEY`` to the latin1 ``assistant_chat_session.session_id`` is
rejected (errno 3780) when the new table inherits the utf8mb4 DB default, and
MySQL's non-transactional DDL left the table half-created with 0007 forever
unrecorded (retrying 1050 on every boot). Rewriting the applied migration is
safe here: ``state_operations`` carry the ORIGINAL CreateModel verbatim, so
recorded deployments see identical model state (`makemigrations nextseek_api
--check` stays clean), and Django never re-runs recorded migrations. The
database side is the shared idempotent heal (see _cc_transcript_heal), which
also converges half-created/wedged schemas without any manual --fake.
Deployments where 0007 is already recorded are healed by 0008 instead.
"""
import django.db.models.deletion
from django.db import migrations, models

from ._cc_transcript_heal import heal, unheal


class Migration(migrations.Migration):

    # MySQL DDL auto-commits; the heal is written to be resumable mid-way
    # (same precedent as 0005_ensure_chatsession_extra_state_column).
    atomic = False

    dependencies = [
        ("nextseek_api", "0006_merge_extra_state_guards"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
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
            ],
            database_operations=[
                # Reverse drops the child table (original CreateModel
                # semantics) so `migrate nextseek_api <0007` doesn't strand
                # the FK and wedge 0001's parent DROP (errno 3730).
                migrations.RunPython(heal, unheal),
            ],
        ),
    ]
