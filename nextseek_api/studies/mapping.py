"""The clones' internal-assay rows (tool spec 7, row 4; T8).

A clone A' gets a copy of each of its source assay A's ``assays_internal_assays`` rows, so its edges resolve to the
same internal assay; they are written through the ORM on the dmac database in one transaction, before any link moves.
Only pairs not yet present are inserted, so a rerun inserts nothing. The rollback deletes a journaled row only while it
still holds its journaled pair. The caller enqueues ``assay_map`` (writer WR-33).
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from seek.models import Assays_internal_assays


def insert_clone_mappings(pairs) -> list[list[int]]:
    wanted = sorted({(int(a), int(i)) for a, i in pairs})
    if not wanted:
        return []
    db = settings.NEXTSEEK_DATABASE
    assay_ids = sorted({a for a, _i in wanted})
    with transaction.atomic(using=db):
        have = set(Assays_internal_assays.objects.using(db).filter(assay_id__in=assay_ids)
                   .values_list("assay_id", "internal_assay_id"))
        new = [Assays_internal_assays(assay_id=a, internal_assay_id=i) for a, i in wanted if (a, i) not in have]
        if new:
            Assays_internal_assays.objects.using(db).bulk_create(new)
    wanted_set = set(wanted)
    rows = Assays_internal_assays.objects.using(db).filter(assay_id__in=assay_ids).values_list(
        "id", "assay_id", "internal_assay_id")
    return sorted([int(r), int(a), int(i)] for r, a, i in rows if (a, i) in wanted_set)


def rows_holding(pairs) -> list[list[int]]:
    """The ``[id, assay_id, internal_assay_id]`` rows that hold ``pairs`` now: a clone's rows a crash left out of
    ``map.done``."""
    wanted = {(int(a), int(i)) for a, i in pairs}
    if not wanted:
        return []
    rows = Assays_internal_assays.objects.using(settings.NEXTSEEK_DATABASE).filter(
        assay_id__in=sorted({a for a, _i in wanted})).values_list("id", "assay_id", "internal_assay_id")
    return sorted([int(r), int(a), int(i)] for r, a, i in rows if (a, i) in wanted)


def delete_clone_mappings(rows) -> dict:
    db = settings.NEXTSEEK_DATABASE
    deleted = 0
    not_deleted: list[int] = []
    with transaction.atomic(using=db):
        for row_id, assay_id, internal_id in rows:
            n, _ = Assays_internal_assays.objects.using(db).filter(
                id=row_id, assay_id=assay_id, internal_assay_id=internal_id).delete()
            if n:
                deleted += n
            else:
                not_deleted.append(int(row_id))
    return {"deleted": deleted, "not_deleted": not_deleted}
