"""The chat question and title columns become utf8mb4 on boxes seeded in latin1.

On dev and prod ``assistant_query_task.query`` is latin1, so a question with "−" or "β" fails
its INSERT with MySQL error 1366 and the async query endpoint answers 500. The heal
(``_chat_text_utf8mb4``) converts only the free-text columns, leaves the key columns that the
chat foreign keys match on, and does nothing where the columns are utf8mb4 already. Database
only: the model state does not change. Non-atomic because the heal runs DDL from RunPython (see
nextseek_api/tests/test_runpython_ddl_atomicity.py). Reversible as a no-op: going back to
latin1 would lose the characters this exists to keep.
"""
from django.db import migrations

from ._chat_text_utf8mb4 import heal


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("nextseek_api", "0033_clear_staff_non_superusers"),
    ]

    operations = [
        migrations.RunPython(heal, migrations.RunPython.noop),
    ]
