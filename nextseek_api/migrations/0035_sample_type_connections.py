# The definitions connections table (nextseek_api/graph_sync/models_db.py, SampleTypeConnectionRow): one row per
# licensed (parent type, assay, child type) triple, written by graph_sync's context step. Follows 0034, the single
# head on origin/dev when this was written.

from django.db import migrations, models


def _byte_exact(apps, schema_editor):
    """utf8mb4_bin on MySQL: codes and assay titles compare byte for byte, as graph_sync's titles do, whatever the
    schema's default charset (dev's and production's dmac schemas hold legacy latin1 tables). Under a case-insensitive
    collation two titles differing only in case or a trailing space would collide on the unique key."""
    if schema_editor.connection.vendor == "mysql":
        schema_editor.execute("ALTER TABLE sample_type_connections CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")


class Migration(migrations.Migration):

    # The collation change is DDL inside RunPython (nextseek_api/tests/test_runpython_ddl_atomicity.py).
    atomic = False

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
        migrations.RunPython(_byte_exact, migrations.RunPython.noop),
    ]
