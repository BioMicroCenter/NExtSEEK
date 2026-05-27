"""Stage 4: TRANSFORM — Convert InputRowModel to InsertableSample."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy.engine import Connection

from .errors import AttributeNameError, JsonNormalizationError
from .helpers import UID_RE
from .identity import canonicalize_file_primary_data, extract_identity
from .models import InputRowModel, InsertableSample, SampleMetadata
from .prefetch import (
    prefetch_sample_type_attributes,
    resolve_sample_type_id,
    validate_assay_ids,
)

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

    def _json_dumps_min(obj):
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    _json_loads = json.loads

    def _json_dumps_min(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def build_insertable(
    row: InputRowModel,
    project_id: int,
    conn: Connection,
) -> Tuple[InsertableSample, Dict[str, List[str]]]:
    """Transform an InputRowModel into an InsertableSample.

    Returns (InsertableSample, warnings_dict) where warnings_dict maps
    warning categories to lists of messages.
    """
    warnings: Dict[str, List[str]] = {}

    # 1. Effective project_id (per-row override > global default)
    effective_pid = row.project_id if row.project_id is not None else project_id

    # 2. Parse and minify JSON metadata
    try:
        minified = parse_and_minify_json(row.json_metadata)
    except JsonNormalizationError:
        raise

    try:
        parsed_meta = _json_loads(minified)
    except Exception:
        parsed_meta = {}
    if not isinstance(parsed_meta, dict):
        parsed_meta = {}
    canonical_meta = canonicalize_file_primary_data(parsed_meta)

    # 3. Resolve sample type (needed for attribute-name check)
    sample_type_id = resolve_sample_type_id(row.SampleType, effective_pid, conn)

    # 4. Verify json_metadata keys are defined attributes for this SampleType.
    #    No skip-list — every key (including SEEK-conventional UID/Parent/Protocol)
    #    must exist in sample_attributes for the row's SampleType.
    if canonical_meta:
        allowed_attrs = prefetch_sample_type_attributes(
            [sample_type_id], conn
        ).get(sample_type_id, set())
        bad_keys = sorted(k for k in canonical_meta.keys() if k not in allowed_attrs)
        if bad_keys:
            raise AttributeNameError(
                sample_type=row.SampleType,
                sample_type_id=sample_type_id,
                bad_keys=bad_keys,
            )

    minified = unicodedata.normalize("NFC", _json_dumps_min(canonical_meta))

    # 5. Title from metadata
    try:
        meta = SampleMetadata.model_validate(canonical_meta)
    except Exception:
        meta = SampleMetadata()
    title = title_from_metadata(meta, row.UID)

    # 6. Validate assay IDs
    valid_assays, missing_assays = validate_assay_ids(row.assay_ids, conn)
    if missing_assays:
        warnings.setdefault("missing_assays", []).append(
            f"Assay IDs not found: {sorted(missing_assays)}"
        )

    # 6. Construct InsertableSample
    sample = InsertableSample(
        uuid=row.UID,
        title=title or "Undefined",
        sample_type_id=sample_type_id,
        json_metadata=minified,
        assay_ids=sorted(valid_assays),
    )

    return sample, warnings


def parse_and_minify_json(raw: str) -> str:
    """Strip BOM, parse, and re-serialize JSON with minimal whitespace.

    Raises JsonNormalizationError on malformed input.
    """
    try:
        s = raw.lstrip("\ufeff").strip() if isinstance(raw, str) else str(raw)
        if not s:
            return "{}"
        parsed = _json_loads(s)
        minified = _json_dumps_min(parsed)
        # NFC normalize the output
        return unicodedata.normalize("NFC", minified)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise JsonNormalizationError(f"Cannot parse JSON metadata: {exc}") from exc


def title_from_metadata(metadata: SampleMetadata, uid: Optional[str]) -> Optional[str]:
    """Determine sample title from canonical identity rules."""
    normalized_uid = _normalize_text(uid) if uid and str(uid).strip() else None
    identity = extract_identity(
        metadata.model_dump(exclude_none=True),
        uid=normalized_uid if normalized_uid and UID_RE.match(normalized_uid) else None,
    )
    if identity is not None:
        return _normalize_text(identity)
    if normalized_uid is not None:
        return normalized_uid
    return None


def _normalize_text(s: str) -> str:
    """NFC-normalize, strip, and collapse whitespace."""
    s = unicodedata.normalize("NFC", s)
    s = s.strip()
    s = _WHITESPACE_RE.sub(" ", s)
    return s
