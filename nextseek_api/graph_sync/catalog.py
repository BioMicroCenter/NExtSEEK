"""The SampleType and Attribute catalog for graph schema v1.1 (docs/neo4j-schema.md, section "v1.1").

Pure: no database. The readers in ``sources.py`` supply the rows, and the writer stores each returned dict as a
node's whole property map (``SET t = r``). So every value here is flat and storable in Neo4j: a str, an int, a bool,
or a non-empty list of str. Nothing is ever None: empty is absent, as it is on Sample nodes.

Rules this module keeps:

- **Titles are byte-exact.** MySQL compares titles case-insensitively and ignores trailing spaces; Neo4j property
  names are exact. Every lookup here is a Python dict or set lookup on the title as stored, so ``Organ`` never takes
  the meaning of ``organ`` and ``Manufacturer `` keeps its trailing space in ``title`` and ``key``.
- **Context joins by code.** A SampleType's curated card is the ``dmac.sample_types_context`` row whose
  ``sample_type`` equals the type's title, never the one whose ``sampletype_id`` equals its id (ids differ across
  instances). A type with no row is a normal state: ``has_context`` is False and no context property is written.
- **Clade** is the context row's own clade when it has one (what the catalog pages and the entity tree show), else
  the type's ``sample_types_clades`` entry, which covers every type, the ones without a context row included.
- **Meaning** is the global ``dmac.sample_attributes_unique`` entry for the attribute's exact title. SEEK's own
  ``description`` is kept beside it as ``seek_description``; the two are not merged.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from nextseek_api.graph_sync.projection import is_empty, label_for, value_type_for
from nextseek_api.services.context_catalog import parse_alternation, parse_list

# SampleType properties that exist only when the title has a sample_types_context row.
CONTEXT_PROPERTIES = ("name", "summary", "tags", "curated_parents", "curated_children")
# Joins the curated parent and child codes into one string property.
CURATED_SEPARATOR = " | "

_FILE_PREFIXES = ("File_", "Checksum_", "Link_")
# Longest first, so "Temp_Units" names "Temp" before "Temp_" is tried.
_UNIT_SUFFIXES = ("_Units", "Units", "_Unit", "Unit")
# A name Cypher accepts without backticks. Conservative: anything else, including a leading underscore or a
# non-ASCII letter, is reported as needing them, and backticks are always valid.
_PLAIN_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def role_for(title: str) -> str:
    """What an attribute is for, from its title alone; the first rule that matches wins.

    ``lineage``: the title contains "parent" in any case (the batch-upload parent-token rule, so these keys also
    produce DERIVED_FROM edges). ``file``: it starts ``File_``, ``Checksum_`` or ``Link_``. ``identifier``: it is
    ``UID``. ``unit``: it ends ``Units``, ``_Units``, ``Unit`` or ``_Unit``. Otherwise ``data``.
    """
    if "parent" in title.lower():
        return "lineage"
    if title.startswith(_FILE_PREFIXES):
        return "file"
    if title == "UID":
        return "identifier"
    if title.endswith(_UNIT_SUFFIXES):
        return "unit"
    return "data"


def attribute_key(sample_type_id: int, title: str) -> str:
    """An Attribute's unique key, ``"<sample_type_id>:<title>"``, with the title exactly as stored."""
    return f"{int(sample_type_id)}:{title}"


def needs_backticks(title: str) -> bool:
    """Whether a property named ``title`` must be backtick-quoted in Cypher."""
    return _PLAIN_NAME_RE.fullmatch(title) is None


def _put(props: dict, key: str, value) -> None:
    """Set ``key`` only when ``value`` is not empty (None, "", an empty list or map)."""
    if not is_empty(value):
        props[key] = value


def _curated(raw, known: set[str]) -> str:
    """Codes named in a curated parents or children column, in order, once each, joined by " | ".

    Parsed by ``context_catalog.parse_alternation``: codes that are not a known sample type title are dropped,
    and alternatives ("MUS or PAV") are listed like the rest, as the catalog pages list them.
    """
    codes: list[str] = []
    for group in parse_alternation(raw, known):
        for code in group:
            if code not in codes:
                codes.append(code)
    return CURATED_SEPARATOR.join(codes)


def assert_labels_unique(sample_types) -> None:
    """Raise ValueError naming the titles when two sample types would share a ``T_`` label.

    Accepts built SampleType rows (their ``label``) or raw type rows (the label is derived from ``title``).
    """
    titles_by_label: dict[str, list[str]] = defaultdict(list)
    for sample_type in sample_types:
        label = sample_type.get("label") or label_for(sample_type["title"])
        titles_by_label[label].append(sample_type["title"])
    collisions = {label: titles for label, titles in titles_by_label.items() if len(titles) > 1}
    if collisions:
        detail = "; ".join(f"{label} from {', '.join(repr(t) for t in titles)}"
                           for label, titles in sorted(collisions.items()))
        raise ValueError(f"sample type labels collide: {detail}")


def build_sample_types(types: list[dict], context: dict[str, dict], clades: dict[int, str],
                       deprecated: set[str]) -> list[dict]:
    """One SampleType property map per SEEK sample type, in input order.

    ``types`` rows have ``id``, ``title``, ``uuid`` and ``description``. ``context`` maps a title, byte-exact, to its
    ``sample_types_context`` row (``name``, ``description``, ``tags``, ``parent_sampletypes``,
    ``child_sampletypes`` and, when read, ``clade``). ``clades`` maps a type id to its ``sample_types_clades``
    title. ``deprecated`` holds the titles SEEK marks retired.

    Raises ValueError when a type has no title (it could have no label) or when two labels collide.
    """
    known = {t["title"] for t in types if not is_empty(t.get("title"))}
    rows = []
    for sample_type in types:
        type_id = int(sample_type["id"])
        title = sample_type.get("title")
        if is_empty(title):
            raise ValueError(f"sample type {type_id} has no title, so it can have no label")
        props = {"id": type_id, "title": title, "label": label_for(title)}
        _put(props, "uuid", sample_type.get("uuid"))
        _put(props, "seek_description", sample_type.get("description"))
        props["deprecated"] = title in deprecated
        row = context.get(title)
        props["has_context"] = row is not None
        clade = None
        if row is not None:
            _put(props, "name", row.get("name"))
            _put(props, "summary", row.get("description"))
            _put(props, "tags", parse_list(row.get("tags")))
            _put(props, "curated_parents", _curated(row.get("parent_sampletypes"), known))
            _put(props, "curated_children", _curated(row.get("child_sampletypes"), known))
            clade = row.get("clade")
        if is_empty(clade):
            clade = clades.get(type_id)
        _put(props, "clade", clade)
        rows.append(props)
    assert_labels_unique(rows)
    return rows


def _unit_key(sample_type_id: int, title: str, titles_on_type: set[str]) -> str | None:
    """The key of the attribute a unit attribute qualifies (``CellCountUnits`` to ``CellCount``), on the same type.

    Each unit suffix is tried, longest first, until the remaining title names an attribute of the same type.
    None when none does.
    """
    for suffix in _UNIT_SUFFIXES:
        if title.endswith(suffix):
            base = title[:-len(suffix)]
            if base and base in titles_on_type:
                return attribute_key(sample_type_id, base)
    return None


def build_attributes(attrs: list[dict], attr_types: dict[int, dict], meanings: dict[str, str],
                     type_titles: dict[int, str]) -> list[dict]:
    """One declared Attribute property map per SEEK ``sample_attributes`` row, in input order.

    ``attrs`` rows have ``id``, ``sample_type_id``, ``title``, ``pos``, ``required``, ``is_title``,
    ``sample_attribute_type_id`` and ``description``. ``attr_types`` maps a ``sample_attribute_types`` id to its row
    (``base_type``); an attribute whose type is unknown gets no ``base_type`` and ``value_type`` ``string``, so its
    values are stored as they come. ``meanings`` maps a title, byte-exact, to its global meaning. ``type_titles``
    maps a sample type id to its title.

    Raises ValueError when an attribute has no title, names a sample type not in ``type_titles``, or shares its
    (sample type, title) key with another attribute (MySQL enforces none of these).
    """
    checked = []
    for attr in attrs:
        attr_id = int(attr["id"])
        type_id = attr.get("sample_type_id")
        if type_id is None or int(type_id) not in type_titles:
            raise ValueError(f"sample attribute {attr_id} names sample type {type_id}, which is not in the catalog")
        title = attr.get("title")
        if is_empty(title):
            raise ValueError(f"sample attribute {attr_id} has no title")
        checked.append((attr, attr_id, int(type_id), title))

    titles_on_type: dict[int, set[str]] = defaultdict(set)
    for _, _, type_id, title in checked:
        titles_on_type[type_id].add(title)

    rows = []
    ids_by_key: dict[str, list[int]] = defaultdict(list)
    for attr, attr_id, type_id, title in checked:
        attr_type_id = attr.get("sample_attribute_type_id")
        attr_type = attr_types.get(int(attr_type_id)) if attr_type_id is not None else None
        base_type = (attr_type or {}).get("base_type")
        role = role_for(title)
        props = {"key": attribute_key(type_id, title), "id": attr_id, "sample_type_id": type_id,
                 "sample_type": type_titles[type_id], "title": title}
        if attr.get("pos") is not None:
            props["pos"] = int(attr["pos"])
        props["required"] = bool(attr.get("required"))
        props["is_title"] = bool(attr.get("is_title"))
        _put(props, "base_type", base_type)
        props["value_type"] = value_type_for(base_type)
        props["declared"] = True
        _put(props, "seek_description", attr.get("description"))
        _put(props, "meaning", meanings.get(title))
        props["role"] = role
        if role == "unit":
            _put(props, "unit_key", _unit_key(type_id, title, titles_on_type[type_id]))
        props["needs_backticks"] = needs_backticks(title)
        ids_by_key[props["key"]].append(attr_id)
        rows.append(props)

    duplicates = {key: ids for key, ids in ids_by_key.items() if len(ids) > 1}
    if duplicates:
        detail = "; ".join(f"{key!r} on attributes {ids}" for key, ids in sorted(duplicates.items()))
        raise ValueError(f"sample attributes share a (sample type, title) key: {detail}")
    return rows


def undeclared_attribute(sample_type_id: int, sample_type: str, title: str) -> dict:
    """An Attribute for a key found on samples of a type but not declared on it in SEEK.

    ``declared`` is False and there is no ``id``. ``value_type`` is ``string``, which is how the projection stores
    a key its type does not declare.
    """
    return {"key": attribute_key(sample_type_id, title), "sample_type_id": int(sample_type_id),
            "sample_type": sample_type, "title": title, "value_type": "string", "declared": False,
            "role": role_for(title), "needs_backticks": needs_backticks(title)}


def catalog_hash(sample_types: list[dict], attributes: list[dict]) -> str:
    """sha256 hex digest of what the query side caches from the catalog.

    The canonical JSON of the sorted (type id, title, label) and (key, value_type, declared) tuples, so it is
    independent of input order and changes when a type, a label, an attribute, a ``value_type`` or ``declared``
    changes, but not when descriptive text such as ``meaning`` does.
    """
    payload = {
        "sample_types": sorted([int(t["id"]), t["title"], t["label"]] for t in sample_types),
        "attributes": sorted([a["key"], a["value_type"], bool(a["declared"])] for a in attributes),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()
