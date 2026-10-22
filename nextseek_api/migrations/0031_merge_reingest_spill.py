from django.db import migrations


class Migration(migrations.Migration):
    """Closes the fork the reingest integration branch left when it merged dev: its 0021-0023 (pipeline runs, the
    reingest attribute proposals, and their merge) beside dev's 0023-0030. No operations."""

    dependencies = [
        ("nextseek_api", "0023_merge_reingest_graph_sync"),
        ("nextseek_api", "0030_spill_json_fields"),
    ]

    operations = []
