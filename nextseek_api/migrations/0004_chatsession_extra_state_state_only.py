"""State-only migration: declares extra_state on the Django model.

The MySQL column was added by a now-rewritten earlier migration and already
exists in the database. This migration uses SeparateDatabaseAndState to
update Django's model state ONLY, without issuing any DDL. Running
``manage.py migrate`` will mark this as applied with no schema changes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0003_chatsession_title'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='chatsession',
                    name='extra_state',
                    field=models.JSONField(default=dict),
                ),
            ],
            database_operations=[],   # column already exists; no DDL
        ),
    ]
