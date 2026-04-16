"""Shared helpers for deriving non-UID sample identity values."""
from __future__ import annotations

from typing import Any, Mapping, Optional


IDENTITY_FIELDS = (
    "Name",
    "name",
    "File_PrimaryData",
    "File_PrimartyData",
    "File_PrimaryData_Forward",
    "File_PrimartyData_Forward",
    "File_PrimaryData_Reverse",
    "File_PrimartyData_Reverse",
)

_CANONICAL_FILE_FIELDS = (
    "File_PrimaryData",
    "File_PrimaryData_Forward",
    "File_PrimaryData_Reverse",
)

_TYPO_TO_CANONICAL = {
    "File_PrimartyData": "File_PrimaryData",
    "File_PrimartyData_Forward": "File_PrimaryData_Forward",
    "File_PrimartyData_Reverse": "File_PrimaryData_Reverse",
}

_FILE_BASED_PREFIXES = ("D.", "A.")


def _clean_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _is_file_based_class(*, uid: Optional[str], sample_type: Optional[str]) -> bool:
    if uid and "-" in uid:
        prefix = uid.split("-", 1)[0]
        return prefix.startswith(_FILE_BASED_PREFIXES)
    if sample_type:
        prefix = sample_type.split("_", 1)[0]
        return prefix.startswith(_FILE_BASED_PREFIXES)
    return False


def canonicalize_file_primary_data(meta: Any) -> Any:
    """Return a copy of metadata with typo-spelled file fields canonicalized."""
    if not isinstance(meta, dict):
        return meta

    canonicalized = dict(meta)
    for typo_key, canonical_key in _TYPO_TO_CANONICAL.items():
        if canonical_key in canonicalized:
            canonicalized.pop(typo_key, None)
            continue
        if typo_key in canonicalized:
            canonicalized[canonical_key] = canonicalized.pop(typo_key)
    return canonicalized


def canonicalize_identity_metadata(meta: Any) -> Any:
    """Return a copy of metadata with identity keys normalized to canonical names."""
    if not isinstance(meta, dict):
        return meta

    canonicalized = canonicalize_file_primary_data(meta)
    if "Name" not in canonicalized and "name" in canonicalized:
        canonicalized["Name"] = canonicalized.pop("name")
    elif "Name" in canonicalized:
        canonicalized.pop("name", None)
    return canonicalized


def extract_identity(
    meta: Mapping[str, Any] | None,
    *,
    uid: Optional[str] = None,
    sample_type: Optional[str] = None,
) -> Optional[str]:
    """Extract the stable non-UID identity for a sample.

    File-based classes (UID/sample-type prefixes starting with ``D.`` or ``A.``)
    prefer the canonical File_PrimaryData family, then typo variants, and only
    then fall back to Name. Malformed UIDs fall back to sample_type or the
    default Name-first precedence.
    """
    if not isinstance(meta, Mapping):
        return None

    canonical_meta = canonicalize_identity_metadata(dict(meta))
    file_based = _is_file_based_class(uid=uid, sample_type=sample_type)

    if file_based:
        for field_name in _CANONICAL_FILE_FIELDS:
            value = _clean_value(canonical_meta.get(field_name))
            if value is not None:
                return value
        for field_name in ("File_PrimartyData", "File_PrimartyData_Forward", "File_PrimartyData_Reverse"):
            value = _clean_value(meta.get(field_name))
            if value is not None:
                return value
        return _clean_value(canonical_meta.get("Name") or canonical_meta.get("name"))

    name = _clean_value(canonical_meta.get("Name"))
    if name is not None:
        return name

    lowercase_name = _clean_value(canonical_meta.get("name"))
    if lowercase_name is not None:
        return lowercase_name

    for field_name in _CANONICAL_FILE_FIELDS:
        value = _clean_value(canonical_meta.get(field_name))
        if value is not None:
            return value

    for field_name in ("File_PrimartyData", "File_PrimartyData_Forward", "File_PrimartyData_Reverse"):
        value = _clean_value(meta.get(field_name))
        if value is not None:
            return value
    return None
