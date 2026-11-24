"""``when_to_use`` and ``similar_types`` on ``sample_types_context`` and ``assay_context``.

Database only: the two tables have no Django migration and these hand columns stay off the model (the Definitions
admin uses raw SQL, as for ``repository_attributes``). The heal (``_definitions_guide_columns``) adds each column
only where the table exists and the column is absent. Non-atomic because it runs DDL from RunPython (see
nextseek_api/tests/test_runpython_ddl_atomicity.py). Reversible as a no-op: dropping the columns would lose the text.
"""
from django.db import migrations

from ._definitions_guide_columns import heal


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("nextseek_api", "0035_sample_type_connections"),
    ]

    operations = [
        migrations.RunPython(heal, migrations.RunPython.noop),
    ]
