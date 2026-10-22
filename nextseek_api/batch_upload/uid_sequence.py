"""Committed per-prefix UID counter shared by the batch and legacy generators."""
from django.db import IntegrityError, OperationalError, models, transaction


class UidSequence(models.Model):
    prefix = models.CharField(max_length=191, unique=True)
    last_index = models.PositiveBigIntegerField(default=0)

    class Meta:
        app_label = "nextseek_api"


def reserve_uid_indexes(prefix: str, n: int, floor: int) -> int:
    """Reserve ``n`` indexes for ``prefix`` and return the first one.

    ``floor`` is the highest index already in ``samples``, so the counter never
    hands out a number that exists. The reservation commits on its own, so a
    later failed insert burns the indexes (gaps) instead of reusing them.
    A first-ever create that races (duplicate key, or a MySQL deadlock, 1213)
    is retried once.
    """
    for attempt in (0, 1):
        try:
            with transaction.atomic(using="default"):
                row, _ = UidSequence.objects.using("default").select_for_update().get_or_create(prefix=prefix)
                start = max(row.last_index, floor) + 1
                row.last_index = start + n - 1
                row.save(update_fields=["last_index"])
            return start
        except IntegrityError:
            if attempt:
                raise
        except OperationalError as exc:
            if attempt or exc.args[0] != 1213:
                raise
