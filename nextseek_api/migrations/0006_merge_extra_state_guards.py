"""Merge the two independent ``extra_state`` column guards into one leaf.

Two branches each added a migration to repair the same defect: ``0004`` is a
state-only ``SeparateDatabaseAndState`` migration, so on a *fresh* database the
``assistant_chat_session.extra_state`` column is never created and every
``ChatSession`` query fails with ``(1054, "Unknown column 'extra_state'")``.

  * ``0005_ensure_chatsession_extra_state_column`` (integration/dmac-assistant,
    authored 2026-06-05): idempotent guard using ``schema_editor.add_field`` with
    ``atomic = False`` (MySQL-only).
  * ``0005_chatsession_extra_state_column`` (feat/native-assistant-granular-ops,
    authored 2026-06-12): idempotent guard using raw ``ADD COLUMN ... JSON NULL``
    with a SQLite fallback; this is the migration exercised by the real-stack
    acceptance suite (``test_granular_realstack``, 8/8).

Both depend on ``0004`` and so form two leaf nodes in the migration graph, which
Django refuses to apply ("Conflicting migrations detected; multiple leaf nodes").
Both are idempotent existence-guards for the *same* column, so running both is
safe (whichever executes first adds the column; the other becomes a no-op). This
merge migration unifies the two leaves without altering or deleting either
guard, preserving both branches' history intact.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0005_chatsession_extra_state_column"),
        ("nextseek_api", "0005_ensure_chatsession_extra_state_column"),
    ]

    operations = []
