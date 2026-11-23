# The definitions connections table (nextseek_api/graph_sync/models_db.py, SampleTypeConnectionRow): one row per
# licensed (parent type, assay, child type) triple, written by graph_sync's context step. Follows 0034, the single
# head on origin/dev when this was written.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0034_chat_text_utf8mb4'),
    ]

    operations = [
        migrations.CreateModel(
            name='SampleTypeConnectionRow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('parent_type', models.CharField(max_length=32)),
                ('assay', models.CharField(max_length=255)),
                ('child_type', models.CharField(max_length=32)),
                ('designed', models.BooleanField(default=False)),
                ('observed', models.BooleanField(default=False)),
            ],
            options={
                'db_table': 'sample_type_connections',
                'constraints': [models.UniqueConstraint(fields=('parent_type', 'assay', 'child_type'),
                                                        name='sample_type_connections_triple')],
            },
        ),
    ]
