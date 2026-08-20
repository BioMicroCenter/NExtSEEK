# Generated for task-08 (Phase-4 Chain-C hardening): T08's own default-database
# migration, depending on T03's 0010_attribute_mutation_job. Adds only the
# T08-owned outbox-dispatcher heartbeat singleton; no duplicate lease columns.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0010_attribute_mutation_job'),
    ]

    operations = [
        migrations.CreateModel(
            name='AttributeOutboxDispatcherHeartbeat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('singleton_key', models.CharField(max_length=64, unique=True)),
                ('owner', models.CharField(max_length=255)),
                ('observed_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('state_version', models.PositiveBigIntegerField(default=0)),
            ],
            options={
                'db_table': 'attributes_outbox_dispatcher_heartbeat',
            },
        ),
    ]
