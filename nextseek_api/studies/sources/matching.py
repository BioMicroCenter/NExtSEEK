"""The shared matching (tool spec 4.3, 4.4): the only place the three sources meet before the core.

- A UID becomes a sample id through the registration resolver's rule, COUNT not EXISTS: a UID on two rows is
  ``sample_uid_not_unique``, never a guess.
- An investigation title names exactly one SEEK investigation (case and surrounding whitespace aside), else the whole
  target is ``investigation_unknown``. Investigations are never created.
- A study title names an existing study of the investigation when exactly one carries it; one held only in another
  investigation, one two studies hold, and the bucket are refused for the whole target, as is a blank title.

Nothing here creates or moves anything: it turns raw targets into ``StudyTarget`` and ``Unmatched`` rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from nextseek_api.studies.buckets import is_bucket_title, title_key
from nextseek_api.studies.models import StudyTarget, Unmatched

INVESTIGATION_UNKNOWN = "investigation_unknown"
SAMPLE_UID_NOT_FOUND = "sample_uid_not_found"
SAMPLE_UID_NOT_UNIQUE = "sample_uid_not_unique"
SAMPLE_ID_NOT_FOUND = "sample_id_not_found"
TARGET_IS_BUCKET = "target_is_bucket"
STUDY_TITLE_AMBIGUOUS = "study_title_ambiguous"
STUDY_TITLE_IN_OTHER_INVESTIGATION = "study_title_in_other_investigation"
SEEK_STUDY_NOT_FOUND = "seek_study_id_not_found"
SEEK_STUDY_TITLE_DIFFERS = "seek_study_id_title_differs"
STUDY_NOT_IN_INVESTIGATION = "study_not_in_investigation"
STUDY_TITLE_BLANK = "study_title_blank"
UID_REASONS = frozenset({SAMPLE_UID_NOT_FOUND, SAMPLE_UID_NOT_UNIQUE})


@dataclass
class RawTarget:
    """One study as a source gives it, before matching. ``uids`` or ``sample_ids`` hold ``(value, provenance)``;
    ``submitted`` maps a UID to what the source wrote when that differs (a dev UID before its ``-PUB`` went)."""

    title: str
    investigation_title: Optional[str] = None
    investigation_id: Optional[int] = None
    key: Optional[str] = None
    seek_study_id: Optional[int] = None
    description: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    uids: list = field(default_factory=list)
    sample_ids: list = field(default_factory=list)
    submitted: dict = field(default_factory=dict)


def clean(value) -> Optional[str]:
    """A cell or property as text, stripped; None when blank. A number becomes its digits."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def existing_study(raw: RawTarget, investigation_id: int, studies) -> tuple[Optional[int], Optional[str]]:
    """(the existing SEEK study the target names, or None for a new one; or a refusal reason)."""
    by_id = {s.id: s for s in studies}
    if raw.seek_study_id is not None:
        study = by_id.get(raw.seek_study_id)
        if study is None:
            return None, SEEK_STUDY_NOT_FOUND
        if study.investigation_id != investigation_id:
            return None, STUDY_NOT_IN_INVESTIGATION
        if title_key(study.title) != title_key(raw.title):
            return None, SEEK_STUDY_TITLE_DIFFERS
        if is_bucket_title(study.title):
            return None, TARGET_IS_BUCKET
        return study.id, None
    if is_bucket_title(raw.title):
        return None, TARGET_IS_BUCKET
    same = [s for s in studies if s.investigation_id == investigation_id and title_key(s.title) == title_key(raw.title)]
    if len(same) > 1:
        return None, STUDY_TITLE_AMBIGUOUS
    if len(same) == 1:
        return same[0].id, None
    if any(title_key(s.title) == title_key(raw.title) for s in studies):
        return None, STUDY_TITLE_IN_OTHER_INVESTIGATION
    return None, None


def _one(values) -> Optional[int]:
    values = list(values or ())
    return values[0] if len(values) == 1 else None


def match_targets(raws: list[RawTarget], reader) -> tuple[list[StudyTarget], list[Unmatched]]:
    investigations = reader.investigations()
    by_title: dict[str, list[int]] = {}
    for inv_id, title in investigations.items():
        by_title.setdefault(title_key(title), []).append(inv_id)
    studies = reader.studies()
    all_uids = sorted({u for raw in raws for u, _p in raw.uids})
    counts = reader.uid_counts(all_uids) if all_uids else {}
    unique = [u for u in all_uids if counts.get(u, 0) == 1]
    ids_of = reader.sample_ids_for_uids(unique) if unique else {}
    given = sorted({int(i) for raw in raws for i, _p in raw.sample_ids})
    existing = reader.existing_sample_ids(given) if given else set()

    targets: list[StudyTarget] = []
    unmatched: list[Unmatched] = []
    for raw in raws:
        if raw.investigation_id is not None:
            inv_id = raw.investigation_id if raw.investigation_id in investigations else None
        elif raw.investigation_title is not None:
            inv_id = _one(by_title.get(title_key(raw.investigation_title)))
        else:
            inv_id = None
        key = raw.key or f"sheet:{inv_id if inv_id is not None else '?'}:{title_key(raw.title)}"

        def miss(reason: str, value, provenance: str) -> None:
            unmatched.append(Unmatched(reason=reason, target_key=key,
                                       submitted=str(raw.submitted.get(value, value)), provenance=[provenance]))

        refusal = (STUDY_TITLE_BLANK if not title_key(raw.title)
                   else INVESTIGATION_UNKNOWN if inv_id is None else None)
        seek_id = None
        if refusal is None:
            seek_id, refusal = existing_study(raw, inv_id, studies)
        if refusal is not None:
            for value, provenance in raw.uids:
                miss(refusal, value, provenance)
            for value, provenance in raw.sample_ids:
                miss(refusal, value, provenance)
            continue

        ids: list[int] = []
        provenance_of: dict[str, set[str]] = {}
        for value, provenance in raw.uids:
            n = counts.get(value, 0)
            sid = ids_of.get(value) if n == 1 else None
            if n > 1:
                miss(SAMPLE_UID_NOT_UNIQUE, value, provenance)
            elif sid is None:
                miss(SAMPLE_UID_NOT_FOUND, value, provenance)
            else:
                ids.append(sid)
                provenance_of.setdefault(str(sid), set()).add(provenance)
        for value, provenance in raw.sample_ids:
            if int(value) not in existing:
                miss(SAMPLE_ID_NOT_FOUND, value, provenance)
            else:
                ids.append(int(value))
                provenance_of.setdefault(str(int(value)), set()).add(provenance)
        if not ids:
            continue
        targets.append(StudyTarget(key=key, investigation_id=inv_id, seek_study_id=seek_id, title=raw.title.strip(),
                                   description=clean(raw.description), doi=clean(raw.doi), pmid=clean(raw.pmid),
                                   sample_ids=ids,
                                   provenance={k: sorted(v) for k, v in sorted(provenance_of.items())}))
    return targets, unmatched
