"""The assay layer of graph schema 1.3 (docs/superpowers/specs/2026-09-25-graph-assay-nodes-design.md, sections 4
and 5.2 to 5.3). Pure: no database, no Neo4j, like ``catalog.py`` and ``labels.py``.

- ``build_catalog``: one ``Assay`` property map per ``dmac.internal_assays`` row, the ``ACCEPTED_BY`` and
  ``GENERATES`` rows from the curated catalog (``dmac.assay_context``), and the reports.
- ``internal_by_seek``: the valid mapping, SEEK assay id to its internal assay ids.
- ``run_rows``: the ``RUN_IN`` rows, one per internal assay and SEEK study.
- ``roles_for_pairs`` and ``sample_edge_rows``: the role rule and the rows the writer sends.
- ``encode_role`` and ``decode_role``: one role in 31 bits, for the full sync's packed array.

Rules this module keeps:

- **The Assay node holds catalog facts only** (D7): the same map for every caller, no run id, no count. Empty is
  absent, as on every node graph_sync writes.
- **The catalog is keyed by internal assay id.** A catalog row with no ``internal_assay_id``, or with one
  ``internal_assays`` lacks, makes nothing; several rows for one id: the lowest row id wins. Each is reported.
- **Codes are sample type titles**, parsed by ``context_catalog.parse_alternation`` against the SEEK titles, which
  drops a code it does not know; ``unknown_codes`` reports those. A group index keeps an either-or choice together.
- **The mapping is filtered, never guessed.** A mapping row with no internal assay, an internal id
  ``internal_assays`` lacks, or a SEEK id ``assays`` lacks maps nothing and is reported.
- **The role rule** (5.3): for each DERIVED_FROM pair between two Sample nodes, each SEEK assay both ends hold, and
  each internal assay it maps to, the child is an ``OUTPUT_OF`` and the parent an ``INPUT_TO`` that Assay, with the
  SEEK id in ``seek_assay_ids``. Lineage never runs through an Assay (D1): each end gets its own edge.
- **Other names** split on commas and semicolons outside parentheses, since a name may carry its own list
  ("Antibody-Dependent Functional Profiling (ADFP, systems serology)"); tags split as the catalog pages split them
  (``context_catalog.parse_list``), as ``SampleType.tags`` does.
"""
from __future__ import annotations

from dataclasses import dataclass

from nextseek_api.graph_sync.projection import is_empty
from nextseek_api.services.context_catalog import parse_alternation, parse_list
from nextseek_graph import schema

INPUT_TO, OUTPUT_OF = schema.INPUT_TO, schema.OUTPUT_OF   # the graph contract's names (graph schema 1.3)
REL_TYPES = (INPUT_TO, OUTPUT_OF)
ROLE_SEEK_LIMIT = 1 << 30      # encode_role packs a SEEK assay id below this into 31 bits
EXAMPLES = 20                  # entries kept per report by report_examples

REPORT_KEYS = ("duplicate_titles", "context_rows_without_internal_assay", "context_rows_for_unknown_internal_assay",
               "context_rows_duplicated", "unknown_codes", "mapping_rows_without_internal_assay",
               "mapping_rows_unknown_internal_assay", "mapping_rows_unknown_seek_assay")
_CODE_COLUMNS = ("required_parent_sample_types", "optional_parent_sample_types", "children_sample_types")
_NAME_SEPARATORS = ",;"


@dataclass(frozen=True)
class AssayCatalog:
    nodes: list          # Assay property maps, by id
    accepted_by: list    # {"code", "assay_id", "required", "group"}
    generates: list      # {"assay_id", "code", "group"}
    reports: dict        # REPORT_KEYS to what each found

    @property
    def ids(self) -> list[int]:
        return [node["id"] for node in self.nodes]


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _put(props: dict, key: str, value) -> None:
    if not is_empty(value):
        props[key] = value


def _strip(value):
    return None if value is None else str(value).strip()


def split_names(raw) -> list[str]:
    """A curator's list of names: split on commas and semicolons outside parentheses, stripped, blanks dropped."""
    if raw is None:
        return []
    names, current, depth = [], [], 0
    for ch in str(raw):
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch in _NAME_SEPARATORS and depth == 0:
            names.append("".join(current))
            current = []
        else:
            current.append(ch)
    names.append("".join(current))
    return [name.strip() for name in names if name.strip()]


class _AnyCode:
    """A ``known`` set holding every non-blank code, so ``parse_alternation`` returns every token it reads."""

    def __contains__(self, code) -> bool:
        return bool(code)


def unknown_codes(raw, known) -> list[str]:
    """The codes a curator column names that ``parse_alternation`` drops as unknown, in order, once each."""
    found = [code for group in parse_alternation(raw, _AnyCode()) for code in group if code not in known]
    return list(dict.fromkeys(found))


def _flatten(groups) -> list[str]:
    return list(dict.fromkeys(code for group in groups for code in group))


def _other_names(row: dict, title) -> list[str]:
    names = split_names(row.get("alternative_assay_names"))
    assay_name = _strip(row.get("assay_name"))
    if assay_name:
        names.append(assay_name)
    return [name for name in dict.fromkeys(names) if name != title]


def _row_order(row: dict):
    row_id = row.get("id")
    return (not _is_id(row_id), row_id if _is_id(row_id) else 0)


def _catalog_rows(context_rows, ids: set, reports: dict) -> dict:
    """The one catalog row each internal assay keeps (the lowest row id), reporting the rest."""
    chosen: dict = {}
    for row in sorted(context_rows, key=_row_order):
        internal_id = row.get("internal_assay_id")
        if internal_id is None:
            reports["context_rows_without_internal_assay"].append(row.get("id"))
            continue
        internal_id = int(internal_id)
        if internal_id not in ids:
            reports["context_rows_for_unknown_internal_assay"].append(row.get("id"))
        elif internal_id in chosen:
            reports["context_rows_duplicated"].setdefault(internal_id, [chosen[internal_id].get("id")]).append(
                row.get("id"))
        else:
            chosen[internal_id] = row
    return chosen


def _mapping_reports(pairs, ids: set, seek_ids, reports: dict) -> None:
    for seek_id, internal_id in pairs:
        if internal_id is None:
            reports["mapping_rows_without_internal_assay"].append(seek_id)
        elif internal_id not in ids:
            reports["mapping_rows_unknown_internal_assay"].append([seek_id, internal_id])
        elif seek_ids is not None and seek_id not in seek_ids:
            reports["mapping_rows_unknown_seek_assay"].append([seek_id, internal_id])


def build_catalog(internal_assays, context_rows, known_types, *, pairs=(), seek_ids=None) -> AssayCatalog:
    """The Assay nodes, the catalog edges and the reports.

    ``internal_assays`` are ``sources.internal_assays()`` rows; ``context_rows`` are ``sources.assay_context_rows()``
    (keys lowercased); ``known_types`` the SEEK sample type titles; ``pairs`` and ``seek_ids``, when given, are the
    mapping (``sources.assay_internal_pairs()``) and the SEEK assay ids, for the mapping reports.
    """
    known = set(known_types)
    reports: dict = {key: ({} if key in ("duplicate_titles", "context_rows_duplicated", "unknown_codes") else [])
                     for key in REPORT_KEYS}
    ids: set[int] = set()
    by_title: dict = {}
    for row in internal_assays:
        ids.add(int(row["id"]))
        if not is_empty(row.get("title")):
            by_title.setdefault(row["title"], []).append(int(row["id"]))
    reports["duplicate_titles"] = {title: sorted(found) for title, found in by_title.items() if len(found) > 1}
    chosen = _catalog_rows(context_rows, ids, reports)
    _mapping_reports(pairs, ids, seek_ids, reports)

    nodes, accepted, generates = [], [], []
    for row in sorted(internal_assays, key=lambda r: int(r["id"])):
        internal_id, title = int(row["id"]), row.get("title")
        context = chosen.get(internal_id)
        props = {"id": internal_id}
        _put(props, "title", title)
        if context is not None:
            _put(props, "other_names", _other_names(context, title))
            _put(props, "description", _strip(context.get("description")))
            _put(props, "tags", parse_list(context.get("tags")))
            _put(props, "parent_clade", _strip(context.get("parent_clade_type")))
            _put(props, "child_clade", _strip(context.get("child_clade_type")))
            required = parse_alternation(context.get("required_parent_sample_types"), known)
            optional = parse_alternation(context.get("optional_parent_sample_types"), known)
            children = parse_alternation(context.get("children_sample_types"), known)
            _put(props, "input_types", _flatten(required))
            _put(props, "optional_input_types", _flatten(optional))
            _put(props, "output_types", _flatten(children))
            for is_required, groups in ((True, required), (False, optional)):
                accepted.extend({"code": code, "assay_id": internal_id, "required": is_required, "group": index}
                                for index, group in enumerate(groups) for code in group)
            generates.extend({"assay_id": internal_id, "code": code, "group": index}
                             for index, group in enumerate(children) for code in group)
            bad = [code for column in _CODE_COLUMNS for code in unknown_codes(context.get(column), known)]
            if bad:
                reports["unknown_codes"][internal_id] = list(dict.fromkeys(bad))
        props["has_context"] = context is not None
        nodes.append(props)
    return AssayCatalog(nodes, accepted, generates, reports)


def internal_by_seek(pairs, internal_ids, seek_ids=None) -> dict[int, tuple[int, ...]]:
    """SEEK assay id to its sorted internal assay ids, from the mapping rows that are valid (the module docstring)."""
    found: dict[int, set[int]] = {}
    for seek_id, internal_id in pairs:
        if internal_id is None or internal_id not in internal_ids:
            continue
        if seek_ids is not None and seek_id not in seek_ids:
            continue
        found.setdefault(int(seek_id), set()).add(int(internal_id))
    return {seek_id: tuple(sorted(ids)) for seek_id, ids in sorted(found.items())}


def run_rows(by_seek: dict, assay_studies) -> list[dict]:
    """One RUN_IN row per (internal assay, SEEK study), holding the SEEK assays of that kind in that study.

    ``assay_studies`` is ``sources.assay_studies()``; a SEEK assay with no study, or with no mapping, makes none."""
    grouped: dict[tuple[int, int], set[int]] = {}
    for seek_id, study_id in assay_studies:
        if study_id is None:
            continue
        for assay_id in by_seek.get(seek_id, ()):
            grouped.setdefault((assay_id, int(study_id)), set()).add(int(seek_id))
    return [{"assay_id": assay_id, "study_id": study_id, "seek_assay_ids": sorted(seek_ids)}
            for (assay_id, study_id), seek_ids in sorted(grouped.items())]


def roles_for_pairs(pairs, assays_by_sample, internal_by_seek) -> dict[int, dict[tuple[str, int], set[int]]]:
    """The role rule (module docstring) over DERIVED_FROM ``pairs`` of (child id, parent id) between Sample nodes.

    ``assays_by_sample`` maps a sample id to its SEEK assay ids (``sources.sample_assay_ids_for``). Returns sample id
    to {(relationship type, Assay id): SEEK assay ids}. A self-loop and an end that is not an int id make nothing."""
    roles: dict[int, dict[tuple[str, int], set[int]]] = {}
    for child, parent in pairs:
        if child == parent or not (_is_id(child) and _is_id(parent)):
            continue
        shared = set(assays_by_sample.get(child) or ()) & set(assays_by_sample.get(parent) or ())
        for seek_id in shared:
            for assay_id in internal_by_seek.get(seek_id, ()):
                roles.setdefault(child, {}).setdefault((OUTPUT_OF, assay_id), set()).add(seek_id)
                roles.setdefault(parent, {}).setdefault((INPUT_TO, assay_id), set()).add(seek_id)
    return roles


def sample_edge_rows(roles: dict) -> list[dict]:
    """The writer's rows for ``roles`` (sample id to its role map, which may be empty), by sample id and Assay id.

    A sample with no role gets empty lists, which makes the writer delete its old edges."""
    rows = []
    for sample_id in sorted(roles):
        by_type: dict[str, list] = {INPUT_TO: [], OUTPUT_OF: []}
        for (rel, assay_id), seek_ids in sorted((roles[sample_id] or {}).items()):
            by_type[rel].append({"assay_id": assay_id, "seek_assay_ids": sorted(seek_ids)})
        rows.append({"id": sample_id, "inputs": by_type[INPUT_TO], "outputs": by_type[OUTPUT_OF]})
    return rows


def members_without_role(ids, assays_by_sample, internal_by_seek, roles) -> int:
    """How many memberships of these samples in a mapped SEEK assay give no edge (no lineage inside that run)."""
    count = 0
    for sample_id in ids:
        with_role = {seek_id for seek_ids in (roles.get(sample_id) or {}).values() for seek_id in seek_ids}
        count += sum(1 for seek_id in set(assays_by_sample.get(sample_id) or ())
                     if seek_id in internal_by_seek and seek_id not in with_role)
    return count


def unmapped_seek_assays(member_counts: dict, internal_by_seek: dict) -> dict[int, int]:
    """The SEEK assays with members and no internal mapping, with their member counts (D9: drift reports them)."""
    return {seek_id: n for seek_id, n in sorted(member_counts.items()) if n and seek_id not in internal_by_seek}


def encode_role(seek_id: int, rel_type: str) -> int:
    """One (SEEK assay, role) in 31 bits: the SEEK id shifted left once, the low bit 1 for OUTPUT_OF. The full sync
    pairs it with a sample id through ``run.encode_pair``."""
    if not (_is_id(seek_id) and 0 <= seek_id < ROLE_SEEK_LIMIT):
        raise ValueError(f"SEEK assay id out of range for a role code: {seek_id!r}")
    if rel_type not in REL_TYPES:
        raise ValueError(f"not a sample edge type: {rel_type!r}")
    return (seek_id << 1) | (rel_type == OUTPUT_OF)


def decode_role(code: int) -> tuple[int, str]:
    return code >> 1, (OUTPUT_OF if code & 1 else INPUT_TO)


def report_counts(reports: dict) -> dict[str, int]:
    """One ``assay_report_<key>`` count per report, for a run's counts."""
    return {f"assay_report_{key}": len(reports.get(key) or ()) for key in REPORT_KEYS}


def report_examples(reports: dict) -> dict:
    """Each report cut to ``EXAMPLES`` entries, for a run's report file."""
    out = {}
    for key in REPORT_KEYS:
        value = reports.get(key) or ([] if key not in ("duplicate_titles", "context_rows_duplicated",
                                                       "unknown_codes") else {})
        out[key] = dict(list(value.items())[:EXAMPLES]) if isinstance(value, dict) else list(value)[:EXAMPLES]
    return out
