"""The bucket rule, one function for the studies tool and the registration resolver.

An investigation's bucket is its one study whose title ends in "Unpublished", surrounding whitespace and case aside
(Python's ``str.strip()`` and ``casefold()``, so a trailing no-break space counts as whitespace). An investigation with
no bucket, or with two, is refused by the tool: ``no_bucket``, ``several_buckets``. The Container-CC runtime carries
its own copy of ``BUCKET_TITLE_SUFFIX`` (it cannot import this package); a test pins the two equal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from django.conf import settings
from sqlalchemy import text

BUCKET_TITLE_SUFFIX = "unpublished"
NO_BUCKET = "no_bucket"
SEVERAL_BUCKETS = "several_buckets"


def title_key(title) -> str:
    """A title as every title comparison of the studies tool reads it: stripped and casefolded."""
    return str(title or "").strip().casefold()


def is_bucket_title(title) -> bool:
    return title_key(title).endswith(BUCKET_TITLE_SUFFIX)


@dataclass(frozen=True)
class Buckets:
    """Investigation id to its one bucket study, the investigations with several, and every bucket-titled study."""

    by_investigation: Mapping[int, int] = field(default_factory=lambda: MappingProxyType({}))
    several: Mapping[int, tuple[int, ...]] = field(default_factory=lambda: MappingProxyType({}))
    study_ids: frozenset = frozenset()

    def refusal(self, investigation_id) -> str | None:
        if investigation_id in self.several:
            return SEVERAL_BUCKETS
        if investigation_id not in self.by_investigation:
            return NO_BUCKET
        return None

    def bucket_of(self, investigation_id) -> int | None:
        return self.by_investigation.get(investigation_id)


def buckets_from_rows(rows: Iterable[tuple]) -> Buckets:
    """``rows`` are ``(study_id, investigation_id, title)``; a study with no investigation is a bucket by title only."""
    found: dict[int, list[int]] = {}
    study_ids: set[int] = set()
    for study_id, investigation_id, title in rows:
        if not is_bucket_title(title):
            continue
        study_ids.add(int(study_id))
        if investigation_id is not None:
            found.setdefault(int(investigation_id), []).append(int(study_id))
    one = {inv: ids[0] for inv, ids in found.items() if len(ids) == 1}
    several = {inv: tuple(sorted(ids)) for inv, ids in found.items() if len(ids) > 1}
    return Buckets(by_investigation=MappingProxyType(dict(sorted(one.items()))),
                   several=MappingProxyType(dict(sorted(several.items()))), study_ids=frozenset(study_ids))


def _seek_db() -> str:
    return settings.DATABASES[settings.SEEK_DATABASE]["NAME"]


def bucket_study_ids(conn) -> Buckets:
    """The buckets of every investigation, read on a SQLAlchemy connection to SEEK's schema. Titles are compared in
    Python, never in SQL: MySQL's comparison ignores trailing spaces but keeps a no-break space."""
    rows = conn.execute(text(f"SELECT id, investigation_id, title FROM {_seek_db()}.studies")).fetchall()
    return buckets_from_rows((r[0], r[1], r[2]) for r in rows)
