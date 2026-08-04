"""QA the reingest rows CC composes, before they are rendered into an upload
workbook. Ported from dmac_curation's qa_flat_sheets checks, adapted to operate on
the in-memory row dicts (not a flat sheet).

A row: {"json_metadata": {<attr>: <value>, ...}, "assay_ids": [int, ...]}.
All reingest samples are [NEW] (UID blank / server-minted), so UID-uniqueness is
not checked; parents must resolve to EXISTING input UIDs (the D.SEQ cohort).

Every catalog-derived argument (``known_sampletypes``, ``required_fields``,
``known_attributes``, ``parent_types``) is optional, and ``None`` means "the
catalog could not be loaded" — the corresponding check is skipped and a single
``catalog_unavailable`` advisory is emitted. ``None`` is deliberately distinct
from an empty collection, which means "the catalog says there is nothing here"
and is a real finding. The caller (``granular._build_upload_xlsx``) owns that
decision; this module never reaches for a catalog itself.

Severity split, and why: a blank required field is a HARD blocker because the
server will reject the row, but the same field carrying an intentional
``*** PLACEHOLDER: ... ***`` marker is SOFT — the curator has said "I know, fill
it in later". That is what makes SKILL.md's "never leave it blank" instruction
load-bearing instead of decorative. ``invented_attribute`` is SOFT here because
this module's catalog is a point-in-time snapshot that real rows already
disagree with (live A.SCXP rows carry ``Checksum_Type``; the snapshot lists
``Checksum_PrimaryType``). Hard enforcement belongs against the LIVE schema, via
``nextseek-validate-upload``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Placeholder markers are intentional/deferred (OK); surprise sentinels are flagged.
_PLACEHOLDER_MARKERS = ("*** PLACEHOLDER", "***PLACEHOLDER")
_SURPRISE_SENTINELS = ("XXX", "TODO", "FIXME", "???", "TBD", "UNCONFIRMED")

# Server-minted on upload; never expected in a reingest row.
_SERVER_MINTED = ("UID",)

CLEAN = "CLEAN"
SOFT_FLAG = "SOFT_FLAG"
HARD_REJECT = "HARD_REJECT"


@dataclass
class QaReport:
    disposition: str = CLEAN
    hard: list[str] = field(default_factory=list)   # blockers
    soft: list[str] = field(default_factory=list)   # advisory

    def _finalize(self) -> "QaReport":
        self.disposition = HARD_REJECT if self.hard else (SOFT_FLAG if self.soft else CLEAN)
        return self


def catalog_fields(entry: dict | None) -> dict:
    """Project one ``sampletypes_db.json`` entry into the sets ``qa_rows`` wants.

    The catalog stores field groups as comma-separated strings ("UID, Name,
    Scientist"). Returns ``{"required", "known", "parent_types"}`` where
    ``known`` is Required u Standard u Possible — the full set of attribute
    names the snapshot believes this sample type accepts. An absent entry
    yields all-``None``, i.e. "skip these checks".
    """
    if not isinstance(entry, dict):
        return {"required": None, "known": None, "parent_types": None}
    required = _split_csv(entry.get("Required Metadata"))
    standard = _split_csv(entry.get("Standard Metadata"))
    possible = _split_csv(entry.get("Possible Metadata Fields"))
    return {
        "required": sorted(required - set(_SERVER_MINTED)),
        "known": required | standard | possible,
        "parent_types": _split_csv(entry.get("Parent_SampleTypes")) or None,
    }


def qa_rows(
    rows: list[dict],
    *,
    sample_type: str,
    known_sampletypes: set[str] | None,
    required_fields: list[str] | None = None,
    existing_parent_uids: set[str] | None = None,
    known_attributes: set[str] | None = None,
    parent_types: set[str] | None = None,
) -> QaReport:
    """Validate one sample type's rows. Returns a QaReport (CLEAN/SOFT_FLAG/HARD_REJECT)."""
    report = QaReport()
    existing = existing_parent_uids or set()

    if known_sampletypes is None:
        report.soft.append(
            "catalog_unavailable: sample-type, required-field and attribute checks skipped"
        )
    elif sample_type not in known_sampletypes:
        report.hard.append(f"unknown_sampletype: {sample_type!r}")

    # Names declared anywhere in THIS batch resolve as parents: a reingest batch may
    # contain a sample derived from a sibling it also creates. Collected in a pre-pass
    # because a row may legitimately name a sibling defined after it.
    batch_names = {
        name for name in (
            str((r.get("json_metadata") or {}).get("Name") or "").strip() for r in rows
        ) if name
    }

    intra_names: set[str] = set()
    for i, row in enumerate(rows):
        meta = row.get("json_metadata") or {}

        # Parent resolvability (;-split; skip placeholder markers).
        parent = str(meta.get("Parent") or "").strip()
        if not parent:
            report.hard.append(f"row {i}: blank Parent (reingest outputs must be derived)")
        else:
            for token in (t.strip() for t in parent.split(";") if t.strip()):
                if _is_placeholder(token):
                    continue
                if token not in existing and token not in batch_names:
                    report.hard.append(f"row {i}: parent_uid_not_found: {token!r}")
                    continue
                # Advisory only: the catalog's Parent_SampleTypes is the declared
                # shape, but a curator reingesting a re-analysis may legitimately
                # hang an A.* off another A.*. The server's DAG check is the floor.
                if parent_types and token in existing:
                    prefix = token.split("-", 1)[0]
                    if prefix and prefix not in parent_types:
                        report.soft.append(
                            f"row {i}: parent_type_mismatch: {token!r} is {prefix}, "
                            f"{sample_type} declares parents {sorted(parent_types)}"
                        )

        # Name uniqueness within the batch (if Names are used).
        name = str(meta.get("Name") or "").strip()
        if name:
            if name in intra_names:
                report.hard.append(f"row {i}: duplicate_name: {name!r}")
            intra_names.add(name)

        # Required-field coverage. Blank blocks; an intentional placeholder is advisory.
        for req in (required_fields or []):
            if req in _SERVER_MINTED:
                continue
            value = str(meta.get(req) or "").strip()
            if not value:
                report.hard.append(f"row {i}: missing_required: {sample_type}:{req}")
            elif _is_placeholder(value):
                report.soft.append(f"row {i}: placeholder_required: {sample_type}:{req}")

        # Attributes the snapshot catalog does not know. Advisory — see module docstring.
        if known_attributes is not None:
            for key in meta:
                if key not in known_attributes and key not in _SERVER_MINTED:
                    report.soft.append(f"row {i}: invented_attribute: {sample_type}:{key}")

        # Placeholder sniff.
        for key, value in meta.items():
            text = str(value or "")
            if _is_placeholder(text):
                continue
            for sentinel in _SURPRISE_SENTINELS:
                if sentinel in text:
                    report.soft.append(f"row {i}: surprise_sentinel {sentinel!r} in {key}")
                    break

    return report._finalize()


def _split_csv(value) -> set[str]:
    """Parse one of the catalog's comma-separated field-group strings."""
    if not isinstance(value, str):
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def _is_placeholder(text: str) -> bool:
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)
