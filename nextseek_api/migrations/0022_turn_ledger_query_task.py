"""Link each TurnLedger row to the QueryTask its turn ran as.

A nullable FK to ``assistant_query_task.id`` (a bigint, so none of the latin1/utf8mb4 FK
mismatch that 0007, 0008 and 0010 heal for the char(32) ``session_id`` applies here). Rows
written before this migration keep ``query_task`` NULL: nothing records which task wrote
them, so no backfill is attempted.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0021_graph_sync_outbox_and_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="turnledger",
            name="query_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ledger_rows",
                to="nextseek_api.querytask",
            ),
        ),
    ]
