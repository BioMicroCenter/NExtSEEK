# The graph_sync state tables (nextseek_api/graph_sync/models_db.py). Follows 0020, the single head on dev and
# dev-graph when this was written; a migration added elsewhere must depend on this one or merge with it.

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0020_assayregistrationjob'),
    ]

    operations = [
        migrations.CreateModel(
            name='GraphSyncOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=32)),
                ('key', models.CharField(max_length=191)),
                ('payload', models.JSONField(blank=True, null=True)),
                ('enqueued_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('claimed_by', models.CharField(blank=True, max_length=255, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, null=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, null=True)),
                ('done_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'graph_sync_outbox',
                'indexes': [models.Index(fields=['done_at', 'enqueued_at'], name='graph_sync_outbox_due')],
                'constraints': [models.UniqueConstraint(fields=('kind', 'key'), name='graph_sync_outbox_kind_key')],
            },
        ),
        migrations.CreateModel(
            name='GraphSyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=32)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(default='running', max_length=16)),
                ('watermark_from', models.CharField(blank=True, max_length=64, null=True)),
                ('watermark_to', models.CharField(blank=True, max_length=64, null=True)),
                ('counts_json', models.JSONField(blank=True, null=True)),
                ('drift_json', models.JSONField(blank=True, null=True)),
            ],
            options={
                'db_table': 'graph_sync_run',
                'indexes': [models.Index(fields=['kind', 'status', 'finished_at'], name='graph_sync_run_kind_status')],
            },
        ),
    ]
