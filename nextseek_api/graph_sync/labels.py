"""The DERIVED_FROM label rule: batch upload's, moved into graph_sync and fed from MySQL (sync design 7.3).

Pure: no database, no Neo4j. The rule is `nextseek_api/batch_upload/neo4j_sync.py::build_derived_from_payloads_from_db`
Steps 1 to 3, and on data where the upload sheet says nothing MySQL does not, `edge_labels` returns what that function
returns for the same edge (R5; pinned by `nextseek_api/tests/test_graph_sync_labels.py`):

- **Assays.** The SEEK assays both endpoints share in `assay_assets`, each resolved through the map `sources` reads
  (SEEK assay id to `(internal assay id or None, title)`, the smallest internal id on 1:N). An assay with no internal
  mapping stands for itself: its SEEK id and its `assays.title`, never a null title (R6). An assay the map lacks (an
  `assay_assets` row with no `assays` row) contributes nothing. The smallest internal id wins the singular fields;
  on a tie, the smaller SEEK assay id (batch upload leaves that to set order). The plural lists hold every resolved
  internal assay, sorted by id and de-duplicated, parallel, with `""` for a missing title because a Neo4j list
  cannot hold a null; the singular title keeps its null, because consumers filter on `IS NOT NULL`.
- **Protocol.** The child's stored `Protocol` (or `protocol`) value, classified by the house three-format rule,
  `helpers.parse_protocol_value`, imported rather than copied (batch upload's CLAUDE.md: one definition). A local
  `/sops/<id>` keeps its id even when no `sops` row has it; a title resolves only when exactly one SOP holds it,
  compared stripped and casefolded as `helpers.lookup_sop_ids_by_title` does; a foreign URL resolves to nothing.

Every label set carries all five assay keys and the protocol pair, nulls and empty lists included (R15): never a
subset. `classify` sorts a stored edge against the rule for R14, which lets only `new` edges be written without the
operator's approval; `label_maps_hash` is the digest `GraphMeta.label_maps_hash` holds, so a changed map is seen.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

from nextseek_api.batch_upload.helpers import parse_protocol_value

SINGULAR_ASSAY_KEYS = ("assay_id", "internal_assay_id", "internal_assay_title")
PLURAL_ASSAY_KEYS = ("internal_assay_ids", "internal_assay_titles")
ASSAY_KEYS = SINGULAR_ASSAY_KEYS + PLURAL_ASSAY_KEYS
PROTOCOL_KEYS = ("protocol_id", "protocol_title")
LABEL_KEYS = ASSAY_KEYS + PROTOCOL_KEYS

NEW, EQUAL, PLURAL_MISSING, CHANGED, CLEARED = "new", "equal", "plural_missing", "changed", "cleared"
CLASSES = (NEW, EQUAL, PLURAL_MISSING, CHANGED, CLEARED)


# --- protocol ----------------------------------------------------------------------------------------------------

def protocol_value_of(meta) -> object:
    """The child's `Protocol` value from its metadata (a dict, or the raw `json_metadata` text or bytes).

    Batch upload's read: `Protocol`, else `protocol`, else "". Metadata that does not parse, or is not a JSON object,
    records no protocol.
    """
    if isinstance(meta, (str, bytes, bytearray)):
        try:
            meta = json.loads(meta) if meta else {}
        except ValueError:
            return ""
    if not isinstance(meta, Mapping):
        return ""
    return meta.get("Protocol") or meta.get("protocol") or ""


def _title_key(title) -> str:
    return str(title).strip().casefold()


def sop_title_index(sops_by_id: Mapping[int, str | None]) -> dict[str, tuple[int, ...]]:
    """A SOP title, stripped and casefolded, to every SOP id holding it (sorted); `resolve_protocol`'s third input."""
    index: dict[str, set[int]] = {}
    for sop_id, title in sops_by_id.items():
        if sop_id is None or title is None:
            continue
        index.setdefault(_title_key(title), set()).add(sop_id)
    return {key: tuple(sorted(ids)) for key, ids in index.items()}


def resolve_protocol(protocol_value, sops_by_id: Mapping[int, str | None],
                     sop_ids_by_title: Mapping[str, Iterable[int]] | None = None) -> tuple[int | None, str | None]:
    """`(protocol_id, protocol_title)` for one child's stored `Protocol` value.

    `sops_by_id` is `sops` (id to title); `sop_ids_by_title` is `sop_title_index(sops_by_id)`, built here when
    omitted (pass it once for many edges). An ambiguous or unmatched title, a foreign URL and an empty value give
    `(None, None)`; a local `/sops/<id>` that names no row gives `(id, None)`, as batch upload writes it.
    """
    ref = parse_protocol_value(protocol_value)
    if ref.sop_id is not None:
        return ref.sop_id, sops_by_id.get(ref.sop_id)
    if ref.title is not None:
        if sop_ids_by_title is None:
            sop_ids_by_title = sop_title_index(sops_by_id)
        ids = tuple(sop_ids_by_title.get(_title_key(ref.title), ()))
        if len(ids) == 1:
            return ids[0], sops_by_id.get(ids[0])
    return None, None


# --- assays ------------------------------------------------------------------------------------------------------

def edge_labels(child_assays: Iterable[int] | None, parent_assays: Iterable[int] | None,
                assay_map: Mapping[int, tuple[int | None, str | None]],
                protocol: tuple[int | None, str | None] | None = None) -> dict:
    """The seven label properties of one DERIVED_FROM edge, keyed and ordered as `LABEL_KEYS`.

    `child_assays` and `parent_assays` are each endpoint's SEEK assay ids from `assay_assets`; `assay_map` is
    `sources.resolved_assay_map()`; `protocol` is `resolve_protocol(...)` for the child, or None for no protocol.
    """
    shared = set(child_assays or ()) & set(parent_assays or ())
    resolved: list[tuple[int, str | None, int]] = []
    for seek_id in shared:
        entry = assay_map.get(seek_id)
        if entry is None:
            continue
        internal_id, title = entry
        if internal_id is None:
            internal_id, title = seek_id, title or ""
        resolved.append((internal_id, title, seek_id))
    resolved.sort(key=lambda item: (item[0], item[2]))

    assay_id = internal_assay_id = internal_assay_title = None
    ids: list[int] = []
    titles: list[str] = []
    if resolved:
        internal_assay_id, internal_assay_title, assay_id = resolved[0]
        for internal_id, title, _seek_id in resolved:
            if ids and ids[-1] == internal_id:
                continue
            ids.append(internal_id)
            titles.append(title or "")

    protocol_id, protocol_title = protocol if protocol is not None else (None, None)
    return {"assay_id": assay_id, "internal_assay_id": internal_assay_id,
            "internal_assay_title": internal_assay_title, "internal_assay_ids": ids,
            "internal_assay_titles": titles, "protocol_id": protocol_id, "protocol_title": protocol_title}


# --- comparing a stored edge with the rule ----------------------------------------------------------------------

def _stored_value(stored: Mapping, key: str):
    """A stored property as the rule states it: absent is null (Neo4j stores no null), a list is a list."""
    value = stored.get(key)
    if key in PLURAL_ASSAY_KEYS and value is not None:
        return list(value)
    return value


def differences(stored: Mapping | None, computed: Mapping) -> list[str]:
    """The label properties whose stored value differs from the rule's, in `LABEL_KEYS` order.

    Only the seven label keys are compared (`child_id`, `parent_id` and the legacy `assay_title` are not). Lists
    compare in order: the rule writes them sorted, so the same entries in another order differ. An absent list
    differs from an empty one.
    """
    stored = stored or {}
    return [key for key in LABEL_KEYS if _stored_value(stored, key) != computed.get(key)]


def classify(stored: Mapping | None, computed: Mapping) -> str:
    """One stored edge against the rule (sync design 7.3, R14); `computed` is `edge_labels(...)`.

    - `equal`: every label property matches.
    - `new`: none of the three singular assay fields is stored; the only edges the default write labels.
    - `plural_missing`: the only differences are absent plural lists; the singular fields and the protocol match.
    - `cleared`: every other difference is a stored value the rule would remove (a null, or an empty list).
    - `changed`: a stored value the rule would replace with another.

    A missing plural list never makes an edge `changed` by itself.
    """
    stored = stored or {}
    diff = differences(stored, computed)
    if not diff:
        return EQUAL
    if all(stored.get(key) is None for key in SINGULAR_ASSAY_KEYS):
        return NEW
    rest = [key for key in diff if not (key in PLURAL_ASSAY_KEYS and stored.get(key) is None)]
    if not rest:
        return PLURAL_MISSING
    if all(computed.get(key) in (None, []) for key in rest):
        return CLEARED
    return CHANGED


# --- the map digest ----------------------------------------------------------------------------------------------

def label_maps_hash(assay_map: Mapping[int, tuple[int | None, str | None]],
                    sops_map: Mapping[int, str | None]) -> str:
    """sha256 hex over the resolved assay map and `sops` (id, title), each sorted by id; titles byte-exact."""
    payload = {
        "assays": [[assay_id, entry[0], entry[1]] for assay_id, entry in sorted(assay_map.items())],
        "sops": [[sop_id, title] for sop_id, title in sorted(sops_map.items())],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
