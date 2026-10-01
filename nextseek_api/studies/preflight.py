"""What apply, the graph step and rollback check before they write (tool spec 4.6, 7 row 1, 7.6 step 1, T20, T24).

- ``run_lock``: one run at a time per box. The MySQL named lock ``nextseek_studies``, taken with ``GET_LOCK`` on a
  connection opened for it alone and held until the run ends (a named lock belongs to its session; Django's and batch
  upload's connections may be closed or reused between steps). Not held: the caller refuses, exit 2. SQLite (the
  suite) needs none.
- ``studies_release_refusal``: the studies release is done on this box: its switch reads ``follow`` and its merge
  would act on no id.
- ``graph_version_refusal``: the graph is at the writer's version (``targeted._refusal``).
- ``apply_refusal``: the buckets are still the plan's, the release is done, and for a plan that creates a study SEEK's
  next study id is still above every graph ``Study.id``.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Optional

from django.conf import settings
from sqlalchemy import text

from nextseek_api.batch_upload.db_engine import get_engine
from nextseek_api.graph_sync import targeted

log = logging.getLogger(__name__)

LOCK_NAME = "nextseek_studies"


@contextmanager
def run_lock():
    if "mysql" not in str(settings.DATABASES[settings.SEEK_DATABASE].get("ENGINE", "")):
        yield True
        return
    conn = get_engine().connect()
    try:
        got = conn.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME}).scalar()
        if got != 1:
            yield False
            return
        try:
            yield True
        finally:
            try:
                conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
            except Exception:  # noqa: BLE001 - dropping the connection frees the lock
                log.warning("studies: could not release %s; dropping its connection instead", LOCK_NAME)
                conn.invalidate()
    finally:
        conn.close()


def _switch_follows() -> bool:
    from nextseek_api.graph_sync import study_links

    return study_links.follows_seek()


def _acting_merge_ids(driver, db) -> list[int]:
    """The SEEK study ids the studies release's merge would still act on: the ids its read-only selection
    (``study_merge.plan``, the merge's dry run) gives a kind of ``study_merge.ACTING``."""
    from nextseek_api.graph_sync import study_merge

    kinds = study_merge.plan(driver, db, detail=False)["kinds"]
    return sorted(int(i) for i, kind in kinds.items() if kind in study_merge.ACTING)


def studies_release_refusal(driver, db) -> Optional[str]:
    if not _switch_follows():
        return ("this box's switch NEXTSEEK_GRAPH_SYNC_STUDY_LINKS is not follow: finish the studies release's "
                "rollout on this box first")
    acting = _acting_merge_ids(driver, db)
    if acting:
        return f"the study merge would still act on {len(acting)} id(s) {acting[:20]}: merge first"
    return None


def graph_version_refusal(driver, db) -> Optional[str]:
    refused = targeted._refusal(driver, db)
    if refused is None:
        return None
    return f"the graph is at schema {refused['schema_version']!r}, not the writer's {refused['writer_version']}"


def apply_refusal(plan, targets, driver, db, reader) -> Optional[str]:
    now = reader.buckets()
    for inv in sorted({t.investigation_id for t in targets}):
        if now.bucket_of(inv) != plan.buckets.get(inv):
            return f"investigation {inv}'s bucket is no longer the plan's: plan again"
    refused = studies_release_refusal(driver, db)
    if refused:
        return refused
    if any(t.study.action == "create" for t in targets):
        next_id, graph_max = reader.next_study_id(), reader.graph_max_study_id()
        if graph_max is not None and next_id <= graph_max:
            return (f"SEEK's next study id {next_id} is not above the graph's Study.id {graph_max}: raise SEEK's "
                    "studies AUTO_INCREMENT first")
    return None
