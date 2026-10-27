"""PaperReingestRun.live_key: a unique key per unfinished run of a paper, so the database refuses a second one.

The backfill keys the oldest unfinished run of each paper; a newer duplicate (only possible before this
migration) keeps NULL and stays visible to the operator command.
"""
import hashlib

from django.db import migrations, models

# Frozen here: the states unfinished when this migration was written.
_LIVE_STATES = ("queued", "acquiring", "context", "curating", "verifying", "building", "ready",
                "paused", "applying", "partially_applied")


def paper_key(doi, paper_input):
    """Frozen copy of models_db.paper_key as it was when this migration was written."""
    doi = (doi or "").strip().lower()
    identity = f"doi:{doi}" if doi else f"input:{(paper_input or '').strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _backfill(apps, schema_editor):
    Run = apps.get_model("nextseek_api", "PaperReingestRun")
    taken = set()
    live = Run.objects.filter(state__in=_LIVE_STATES, superseded_by__isnull=True).order_by("created_at", "id")
    for run in live:
        key = paper_key(run.doi, run.paper_input)
        if key not in taken:
            taken.add(key)
            Run.objects.filter(pk=run.pk).update(live_key=key)


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0034_paper_reingest_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="paperreingestrun",
            name="live_key",
            field=models.CharField(blank=True, editable=False, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(_backfill, migrations.RunPython.noop),
    ]
