"""State-only: SEEK 1.18 dropped people.skype_name (its migration DropSkypeNameFromPerson).

SEEK owns that table and seek/dbrouters.py forbids migrating the SEEK alias, so
this changes Django's model state only and never touches any database.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("seek", "0003_project_template_bundles_table")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.RemoveField(model_name="people", name="skype_name")],
            database_operations=[],
        )
    ]
