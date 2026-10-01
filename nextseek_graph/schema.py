"""The NExtSEEK graph contract: the names each graph schema version defines (docs/neo4j-schema.md).

Standard library only, no Cypher text, no I/O at import, and nothing imported from this package, so the file also
loads alone by its path. Every name sits in the group of the version that introduced it (``LABELS_V11``,
``SAMPLE_SYSTEM_PROPERTIES_V12``, ...). A consumer names the groups it reads, so a later version's names reach a
consumer only through that consumer's own edit. ``nextseek_api/tests/test_graph_sync_contract.py`` holds this file
equal to the document of record and to every copy that cannot import it; ``nextseek_graph/README.md`` says how a
schema change lands.
"""
from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final, Mapping

# --- versions ------------------------------------------------------------------------------------
VERSIONS: Final[tuple[str, ...]] = ("1.1", "1.2", "1.3")   # the doc's sections from v1.1 on, oldest first
SCHEMA_VERSION: Final[str] = "1.2"      # what graph_sync writes to GraphMeta.schema_version; one of VERSIONS
READER_MIN_VERSION: Final[str] = "1.1"  # the oldest graph the Nessie catalog reader accepts

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def version_tuple(value: object) -> tuple[int, int] | None:
    """``(major, minor)`` when ``str(value).strip()`` is ``major.minor``; None for None and for anything else."""
    if value is None:
        return None
    match = _VERSION_RE.match(str(value).strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def at_least(value: object, minimum: str) -> bool:
    """True when ``value`` is a version at or above ``minimum`` ("1.10" is at least "1.2").

    Raises ValueError when ``minimum`` is not a version: that is the caller's bug, not the graph's.
    """
    floor = version_tuple(minimum)
    if floor is None:
        raise ValueError(f"not a schema version: {minimum!r}")
    found = version_tuple(value)
    return found is not None and found >= floor


# --- rules of every version ----------------------------------------------------------------------
TYPE_LABEL_PREFIX: Final[str] = "T_"
TYPE_LABEL_PATTERN: Final[str] = "T_[A-Za-z0-9_]+"   # a regex source, no anchors, for consumers to embed
_TYPE_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_]")
_TYPE_LABEL_RE = re.compile(TYPE_LABEL_PATTERN)


def type_label(title: str) -> str:
    """A sample type's label: ``T_`` plus the title with every character outside [A-Za-z0-9_] made ``_``."""
    return TYPE_LABEL_PREFIX + _TYPE_LABEL_UNSAFE.sub("_", title)


def is_type_label(label: object) -> bool:
    """True for a str that fully matches ``TYPE_LABEL_PATTERN``."""
    return isinstance(label, str) and _TYPE_LABEL_RE.fullmatch(label) is not None


BUDGET_INDEX_PREFIX: Final[str] = "gs_"   # graph_sync's per-type budget indexes; no fixed name may start with it

# === 1.1: the graph_search target (docs/neo4j-schema.md "v1.1") ===================================
SAMPLE: Final[str] = "Sample"
ORPHAN_SAMPLE: Final[str] = "OrphanSample"
SAMPLE_TYPE: Final[str] = "SampleType"
ATTRIBUTE: Final[str] = "Attribute"
PROJECT: Final[str] = "Project"
PERSON: Final[str] = "Person"
STUDY: Final[str] = "Study"
INVESTIGATION: Final[str] = "Investigation"
GRAPH_META: Final[str] = "GraphMeta"
LABELS_V11: Final[frozenset[str]] = frozenset({SAMPLE, ORPHAN_SAMPLE, SAMPLE_TYPE, ATTRIBUTE, PROJECT, PERSON,
                                               STUDY, INVESTIGATION, GRAPH_META})

DERIVED_FROM: Final[str] = "DERIVED_FROM"
OF_TYPE: Final[str] = "OF_TYPE"
HAS_ATTRIBUTE: Final[str] = "HAS_ATTRIBUTE"
IN_PROJECT: Final[str] = "IN_PROJECT"
MEMBER_OF: Final[str] = "MEMBER_OF"
IN_STUDY: Final[str] = "IN_STUDY"
IN_INVESTIGATION: Final[str] = "IN_INVESTIGATION"

# DERIVED_FROM's properties, as the v1.0 row lists them and v1.1 keeps them. v1.2's "DERIVED_FROM labels" table fixes
# the seven label keys' order and the all-seven rule; it adds no name.
DERIVED_FROM_ENDPOINT_KEYS: Final[tuple[str, ...]] = ("child_id", "parent_id")
DERIVED_FROM_SINGULAR_ASSAY_KEYS: Final[tuple[str, ...]] = ("assay_id", "internal_assay_id", "internal_assay_title")
DERIVED_FROM_PLURAL_ASSAY_KEYS: Final[tuple[str, ...]] = ("internal_assay_ids", "internal_assay_titles")
DERIVED_FROM_ASSAY_KEYS: Final[tuple[str, ...]] = DERIVED_FROM_SINGULAR_ASSAY_KEYS + DERIVED_FROM_PLURAL_ASSAY_KEYS
DERIVED_FROM_PROTOCOL_KEYS: Final[tuple[str, ...]] = ("protocol_id", "protocol_title")
DERIVED_FROM_LABEL_KEYS: Final[tuple[str, ...]] = DERIVED_FROM_ASSAY_KEYS + DERIVED_FROM_PROTOCOL_KEYS  # the seven

RELATIONSHIPS_V11: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    DERIVED_FROM: frozenset(DERIVED_FROM_ENDPOINT_KEYS + DERIVED_FROM_LABEL_KEYS),
    OF_TYPE: frozenset(),
    HAS_ATTRIBUTE: frozenset(),
    IN_PROJECT: frozenset(),
    MEMBER_OF: frozenset({"has_left", "time_left_at"}),
    IN_STUDY: frozenset(),
    IN_INVESTIGATION: frozenset(),
})
RELATIONSHIP_PATTERNS_V11: Final[tuple[tuple[str, str, str], ...]] = (   # (start label, type, end label)
    (SAMPLE, DERIVED_FROM, SAMPLE),
    (SAMPLE, OF_TYPE, SAMPLE_TYPE),
    (SAMPLE_TYPE, HAS_ATTRIBUTE, ATTRIBUTE),
    (SAMPLE, IN_PROJECT, PROJECT),
    (PERSON, MEMBER_OF, PROJECT),
    (INVESTIGATION, IN_PROJECT, PROJECT),
    (SAMPLE, IN_STUDY, STUDY),
    (STUDY, IN_INVESTIGATION, INVESTIGATION),
)
SAMPLE_SYSTEM_PROPERTIES_V11: Final[frozenset[str]] = frozenset({
    "id", "uuid", "type", "title", "project_ids", "search_text", "synced_at"})
NODE_PROPERTIES_V11: Final[Mapping[str, frozenset[str]]] = MappingProxyType({   # every label but Sample, OrphanSample
    SAMPLE_TYPE: frozenset({"id", "title", "label", "uuid", "seek_description", "deprecated", "sample_count",
                            "attribute_count", "has_context", "name", "summary", "tags", "curated_parents",
                            "curated_children", "clade"}),
    ATTRIBUTE: frozenset({"key", "id", "sample_type_id", "sample_type", "title", "pos", "required", "is_title",
                          "base_type", "value_type", "declared", "seek_description", "meaning", "role", "unit_key",
                          "needs_backticks", "sample_count"}),
    PROJECT: frozenset({"id", "title"}),
    PERSON: frozenset({"id"}),
    STUDY: frozenset({"id", "title", "description", "DOI", "PMID", "seek_study_id"}),
    INVESTIGATION: frozenset({"id", "title", "description", "project_id"}),
    GRAPH_META: frozenset({"schema_version", "catalog_hash", "synced_at"}),
})
# constraints and indexes: (name, label, property)
UNIQUE_CONSTRAINTS_V11: Final[tuple[tuple[str, str, str], ...]] = (
    ("sample_id_unique", SAMPLE, "id"),
    ("sample_type_id_unique", SAMPLE_TYPE, "id"),
    ("sample_type_title_unique", SAMPLE_TYPE, "title"),
    ("sample_type_label_unique", SAMPLE_TYPE, "label"),
    ("attribute_key_unique", ATTRIBUTE, "key"),
    ("attribute_id_unique", ATTRIBUTE, "id"),
    ("project_id_unique", PROJECT, "id"),
    ("person_id_unique", PERSON, "id"),
    ("study_id_unique", STUDY, "id"),
    ("investigation_id_unique", INVESTIGATION, "id"),
)
RANGE_INDEXES_V11: Final[tuple[tuple[str, str, str], ...]] = (
    ("sample_uuid", SAMPLE, "uuid"),
    ("sample_type", SAMPLE, "type"),
    ("study_seek_study_id", STUDY, "seek_study_id"),
)
FULLTEXT_INDEX: Final[str] = "sample_search_text"            # the graph's one fulltext index (1.1)
FULLTEXT_INDEX_ON: Final[tuple[str, str]] = (SAMPLE, "search_text")

# === 1.2: what the sync adds (docs/neo4j-schema.md "v1.2"): properties only =======================
SAMPLE_SYSTEM_PROPERTIES_V12: Final[frozenset[str]] = frozenset({"source_hash", "parent_titles",
                                                                 "parent_title_hashes"})
NODE_PROPERTIES_V12: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    GRAPH_META: frozenset({"label_maps_hash"})})
ORPHAN_SAMPLE_PROPERTIES_V12: Final[frozenset[str]] = frozenset({"orphaned_at"})   # added to what the Sample carried
GRAPHMETA_KEYS: Final[tuple[str, ...]] = ("schema_version", "catalog_hash", "label_maps_hash", "synced_at")
#   the writer's GraphMeta keys at 1.2, in its order: NODE_PROPERTIES_V11 and _V12's GraphMeta sets together

# === not a doc name ===============================================================================
# Allowed by the graph agent's guard on Attribute; no writer sets them. The Nessie catalog reader still reads the four
# range fields when present; the top_values reader was removed.
LEGACY_ATTRIBUTE_STATS: Final[frozenset[str]] = frozenset({
    "top_values", "top_counts", "num_min", "num_max", "date_min", "date_max"})

# === 1.3: the assay nodes (docs/neo4j-schema.md "v1.3") ===========================================
ASSAY: Final[str] = "Assay"
LABELS_V13: Final[frozenset[str]] = frozenset({ASSAY})

INPUT_TO: Final[str] = "INPUT_TO"
OUTPUT_OF: Final[str] = "OUTPUT_OF"
RUN_IN: Final[str] = "RUN_IN"
ACCEPTED_BY: Final[str] = "ACCEPTED_BY"
GENERATES: Final[str] = "GENERATES"

RELATIONSHIPS_V13: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    INPUT_TO: frozenset({"seek_assay_ids"}),
    OUTPUT_OF: frozenset({"seek_assay_ids"}),
    RUN_IN: frozenset({"seek_assay_ids"}),
    ACCEPTED_BY: frozenset({"required", "group"}),
    GENERATES: frozenset({"group"}),
})
RELATIONSHIP_PATTERNS_V13: Final[tuple[tuple[str, str, str], ...]] = (   # (start label, type, end label)
    (SAMPLE, INPUT_TO, ASSAY),
    (SAMPLE, OUTPUT_OF, ASSAY),
    (ASSAY, RUN_IN, STUDY),
    (SAMPLE_TYPE, ACCEPTED_BY, ASSAY),
    (ASSAY, GENERATES, SAMPLE_TYPE),
)
NODE_PROPERTIES_V13: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    ASSAY: frozenset({"id", "title", "other_names", "description", "tags", "parent_clade", "child_clade",
                      "input_types", "optional_input_types", "output_types", "has_context"}),
})
UNIQUE_CONSTRAINTS_V13: Final[tuple[tuple[str, str, str], ...]] = (("assay_id_unique", ASSAY, "id"),)
RANGE_INDEXES_V13: Final[tuple[tuple[str, str, str], ...]] = (("assay_title", ASSAY, "title"),)
# SCHEMA_VERSION stays "1.2" until the v1.3 section's Versioning subsection is written (W11 with R2b).

# === the next version =============================================================================
# A version adds its groups here, each named with its own suffix (LABELS_V13, RELATIONSHIPS_V13,
# RELATIONSHIP_PATTERNS_V13, NODE_PROPERTIES_V13, UNIQUE_CONSTRAINTS_V13, RANGE_INDEXES_V13, ...), and appends itself
# to VERSIONS, in the commit that writes its tables into docs/neo4j-schema.md. SCHEMA_VERSION moves later, with the
# section's Versioning subsection. No existing group changes. nextseek_graph/README.md says how.

__all__ = (
    "VERSIONS", "SCHEMA_VERSION", "READER_MIN_VERSION", "version_tuple", "at_least",
    "TYPE_LABEL_PREFIX", "TYPE_LABEL_PATTERN", "type_label", "is_type_label", "BUDGET_INDEX_PREFIX",
    "SAMPLE", "ORPHAN_SAMPLE", "SAMPLE_TYPE", "ATTRIBUTE", "PROJECT", "PERSON", "STUDY", "INVESTIGATION",
    "GRAPH_META", "LABELS_V11",
    "DERIVED_FROM", "OF_TYPE", "HAS_ATTRIBUTE", "IN_PROJECT", "MEMBER_OF", "IN_STUDY", "IN_INVESTIGATION",
    "DERIVED_FROM_ENDPOINT_KEYS", "DERIVED_FROM_SINGULAR_ASSAY_KEYS", "DERIVED_FROM_PLURAL_ASSAY_KEYS",
    "DERIVED_FROM_ASSAY_KEYS", "DERIVED_FROM_PROTOCOL_KEYS", "DERIVED_FROM_LABEL_KEYS",
    "RELATIONSHIPS_V11", "RELATIONSHIP_PATTERNS_V11", "SAMPLE_SYSTEM_PROPERTIES_V11", "NODE_PROPERTIES_V11",
    "UNIQUE_CONSTRAINTS_V11", "RANGE_INDEXES_V11", "FULLTEXT_INDEX", "FULLTEXT_INDEX_ON",
    "SAMPLE_SYSTEM_PROPERTIES_V12", "NODE_PROPERTIES_V12", "ORPHAN_SAMPLE_PROPERTIES_V12", "GRAPHMETA_KEYS",
    "LEGACY_ATTRIBUTE_STATS",
    "ASSAY", "LABELS_V13", "INPUT_TO", "OUTPUT_OF", "RUN_IN", "ACCEPTED_BY", "GENERATES", "RELATIONSHIPS_V13",
    "RELATIONSHIP_PATTERNS_V13", "NODE_PROPERTIES_V13", "UNIQUE_CONSTRAINTS_V13", "RANGE_INDEXES_V13",
)
