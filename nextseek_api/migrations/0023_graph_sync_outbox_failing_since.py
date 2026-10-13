"""How long a graph_sync outbox row has been failing (graph_sync health checks in CI).

One nullable column, no backfill: a row that failed before this migration gets its time at its next failure, which
its back-off brings within an hour (six for a full sync). Any backfill would guess, since enqueued_at moves on every
re-enqueue. Written by hand: makemigrations would also emit a TurnLedger index rename that has nothing to do with it.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0022_turn_ledger_query_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="graphsyncoutbox",
            name="failing_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
