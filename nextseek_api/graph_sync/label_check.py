"""Compare DERIVED_FROM labels: the rule's against a graph's (plan task V1; sync design 7.3 and 17).

Pure: no database, no Neo4j, no file. `scripts/graph_search/verify_labels.py` feeds it; the tests pin it on small
synthetic label sets (`nextseek_api/tests/test_graph_sync_label_check.py`).

- **Canonical form.** A label map reduced to the seven `labels.LABEL_KEYS` values, in that order: an absent property
  reads as null (Neo4j stores no null), a list becomes a tuple in its stored order (the rule writes lists sorted, so
  another order is a difference), an integral float reads as its int. Every other edge property (`child_id`,
  `parent_id`, the legacy `assay_title`) is ignored.
- **One digest per pair.** `LabelIndex` keeps each declared pair as one 64-bit key and one 64-bit digest of its
  canonical labels, in two sorted arrays, and each distinct label map once: about 16 bytes a pair, where a dict per
  pair would cost tens of times more.
- **Remap.** The TCGA merge renumbered SEEK and internal assay ids (`dmac.gs_remap`): `remap_ids` maps `assay_id`
  through kind `assay` and `internal_assay_id` through kind `internal_assay`, never across. An id with no row becomes
  `Unmapped`, which equals no id. `key_internal_by_title` is the other key: the local id of the stored title.
- **Check (a)**, `SingularCheck`: the three singular assay fields only. The plural lists and the protocol pair are
  counted apart and are never part of its pass condition.
- **Check (b)**, `ClassCheck`: every label property; each edge sorted by `labels.classify` and counted per class,
  per property and per kind of change.

Both checks count an edge whose pair is not declared (`graph_only`), a declared pair with no edge (`rule_only`,
counted at `close`) and a second edge for one pair (`duplicates`).
"""
from __future__ import annotations

import hashlib
import json
from array import array
from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from nextseek_api.graph_sync import labels

LABEL_KEYS = labels.LABEL_KEYS
SINGULAR_KEYS = labels.SINGULAR_ASSAY_KEYS
PROTOCOL_KEYS = labels.PROTOCOL_KEYS

# Each id must fit 31 bits, so a pair fits one signed 64-bit key.
PAIR_ID_LIMIT = 2 ** 31

# How one property differs, stored against the rule.
ABSENT = "absent"                # nothing stored; the rule has a value
ABSENT_EMPTY = "absent_empty"    # nothing stored; the rule has an empty list
CLEARED = "cleared"              # a stored value the rule would remove (null, or an empty list)
CHANGED = "changed"              # a stored value the rule would replace with another

InScope = Callable[[int, int], bool] | None


@dataclass(frozen=True)
class Unmapped:
    """A stored id (or title) the remap has no row for. It equals no id, so its edge cannot match."""
    kind: str
    old: object

    def __str__(self) -> str:
        return f"unmapped {self.kind} {self.old}"


# --- canonical form ----------------------------------------------------------------------------------------------

def _value(value):
    if isinstance(value, (list, tuple)):
        return tuple(_value(item) for item in value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical(label_map: Mapping | None, keys: tuple[str, ...] = LABEL_KEYS) -> tuple:
    """The values of `keys` in order: absent is None, a list a tuple in its stored order, 494.0 is 494."""
    label_map = label_map or {}
    return tuple(_value(label_map.get(key)) for key in keys)


def as_dict(canon: tuple, keys: tuple[str, ...] = LABEL_KEYS) -> dict:
    """A canonical tuple back in `labels.edge_labels`' shape (lists as lists)."""
    return {key: list(value) if isinstance(value, tuple) else value for key, value in zip(keys, canon)}


def digest(canon: tuple) -> int:
    """The first 8 bytes of sha256 over the canonical tuple as JSON, as a signed 64-bit int."""
    encoded = json.dumps(canon, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big", signed=True)


def stored_labels(props: Mapping) -> dict:
    """The label properties of one stored edge, every other property dropped."""
    return {key: props[key] for key in LABEL_KEYS if props.get(key) is not None}


def _jsonable(value):
    if isinstance(value, Unmapped):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _subset(label_map: Mapping | None, keys: tuple[str, ...]) -> dict | None:
    if label_map is None:
        return None
    return {key: _jsonable(label_map.get(key)) for key in keys}


# --- pairs -------------------------------------------------------------------------------------------------------

def encode_pair(child: int, parent: int) -> int:
    """(child, parent) as one int that sorts by child, then parent."""
    child, parent = int(child), int(parent)
    if not (0 <= child < PAIR_ID_LIMIT and 0 <= parent < PAIR_ID_LIMIT):
        raise ValueError(f"pair ({child}, {parent}) is outside 0 to {PAIR_ID_LIMIT - 1}")
    return (child << 32) | parent


def decode_pair(key: int) -> tuple[int, int]:
    return key >> 32, key & 0xFFFFFFFF


# --- remap and title key -----------------------------------------------------------------------------------------

def _remap(value, table: Mapping[int, int], kind: str):
    new = table.get(_value(value))
    return new if new is not None else Unmapped(kind, value)


def remap_ids(stored: Mapping, *, assay_ids: Mapping[int, int], internal_ids: Mapping[int, int]) -> dict:
    """A copy of `stored` with `assay_id` mapped through `assay_ids` and `internal_assay_id` through `internal_ids`.

    A null stays null; an id with no row becomes `Unmapped`. Titles and the plural lists are left as stored: the lists
    of the dev box mix the two id spaces, so they cannot be renumbered.
    """
    out = dict(stored)
    for key, table, kind in (("assay_id", assay_ids, "assay"), ("internal_assay_id", internal_ids, "internal_assay")):
        if out.get(key) is not None:
            out[key] = _remap(out[key], table, kind)
    return out


def key_internal_by_title(stored: Mapping, internal_ids_by_title: Mapping[str, int]) -> dict:
    """A copy of `stored` whose `internal_assay_id` is the local id of its `internal_assay_title`.

    Titles are compared byte for byte. An unknown title becomes `Unmapped`; an edge with no title is left alone.
    """
    out = dict(stored)
    title = out.get("internal_assay_title")
    if title is None:
        return out
    local = internal_ids_by_title.get(title)
    out["internal_assay_id"] = local if local is not None else Unmapped("internal_assay_title", title)
    return out


# --- comparisons -------------------------------------------------------------------------------------------------

def singular_differences(stored: Mapping, computed: Mapping) -> list[str]:
    """The singular assay fields whose stored value differs from the rule's, in `SINGULAR_KEYS` order."""
    return [key for key, have, want in zip(SINGULAR_KEYS, canonical(stored, SINGULAR_KEYS),
                                            canonical(computed, SINGULAR_KEYS)) if have != want]


def property_changes(stored: Mapping, computed: Mapping, keys: tuple[str, ...] = LABEL_KEYS) -> dict[str, str]:
    """Each differing property and how it differs: `absent`, `absent_empty`, `cleared` or `changed`."""
    changes: dict[str, str] = {}
    for key, have, want in zip(keys, canonical(stored, keys), canonical(computed, keys)):
        if have == want:
            continue
        if have is None:
            changes[key] = ABSENT_EMPTY if want == () else ABSENT
        elif want is None or want == ():
            changes[key] = CLEARED
        else:
            changes[key] = CHANGED
    return changes


def plural_shape(stored: Mapping) -> str:
    """What a stored `internal_assay_ids` holds: `absent`, `empty`, `seek_assay_id` (`[assay_id]`), `internal_id`
    (`[internal_assay_id]`) or `other`. Read before any remap; `seek_assay_id` wins when the two ids are equal."""
    ids = stored.get("internal_assay_ids")
    if ids is None:
        return "absent"
    ids = list(_value(ids))
    if not ids:
        return "empty"
    if ids == [_value(stored.get("assay_id"))]:
        return "seek_assay_id"
    if ids == [_value(stored.get("internal_assay_id"))]:
        return "internal_id"
    return "other"


def _has_protocol(label_map: Mapping) -> bool:
    return any(label_map.get(key) is not None for key in PROTOCOL_KEYS)


def _labelled(label_map: Mapping) -> bool:
    return any(label_map.get(key) is not None for key in SINGULAR_KEYS)


# --- the declared pairs ------------------------------------------------------------------------------------------

class LabelIndex:
    """Every declared pair with one digest of its computed labels; each distinct label map kept once.

    `add` every pair, `freeze` once (sorts, drops a repeated pair, refuses one pair declared with two label maps),
    then `find` a pair's position and read its `labels`.
    """

    def __init__(self):
        self._keys = array("q")
        self._digests = array("q")
        self._maps: dict[int, tuple] = {}
        self._frozen = False
        self.duplicates = 0

    def add(self, child: int, parent: int, label_map: Mapping) -> None:
        if self._frozen:
            raise RuntimeError("the index is frozen")
        canon = canonical(label_map)
        code = digest(canon)
        if self._maps.setdefault(code, canon) != canon:
            raise ValueError(f"two label maps share the digest {code}")
        self._keys.append(encode_pair(child, parent))
        self._digests.append(code)

    def freeze(self) -> LabelIndex:
        order = sorted(range(len(self._keys)), key=self._keys.__getitem__)
        keys, digests = array("q"), array("q")
        for position in order:
            key, code = self._keys[position], self._digests[position]
            if keys and keys[-1] == key:
                if digests[-1] != code:
                    raise ValueError(f"pair {decode_pair(key)} was declared with two label maps")
                self.duplicates += 1
                continue
            keys.append(key)
            digests.append(code)
        self._keys, self._digests, self._frozen = keys, digests, True
        return self

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def distinct(self) -> int:
        """How many distinct label maps the pairs carry."""
        return len(self._maps)

    def find(self, child: int, parent: int) -> int:
        """The pair's position, or -1 when it is not declared."""
        if not self._frozen:
            raise RuntimeError("freeze() the index before reading it")
        try:
            key = encode_pair(child, parent)
        except ValueError:
            return -1
        position = bisect_left(self._keys, key)
        return position if position < len(self._keys) and self._keys[position] == key else -1

    def pair(self, position: int) -> tuple[int, int]:
        return decode_pair(self._keys[position])

    def labels(self, position: int) -> dict:
        """The computed labels of the pair at `position`, in `labels.edge_labels`' shape."""
        return as_dict(self._maps[self._digests[position]])


class _PairCheck:
    """What both checks share: one `seen` byte per declared pair, duplicates, and the unseen pairs at `close`."""

    def __init__(self, index: LabelIndex, example_cap: int):
        self.index = index
        self.example_cap = example_cap
        self.seen = bytearray(len(index))
        self.duplicates = 0
        self._result: dict | None = None

    def _position(self, child: int, parent: int) -> int | None:
        """The pair's position; -1 when it is not declared; None for a second edge of one pair."""
        position = self.index.find(child, parent)
        if position < 0:
            return -1
        if self.seen[position]:
            self.duplicates += 1
            return None
        self.seen[position] = 1
        return position

    def _unseen(self, in_scope: InScope) -> Iterator[tuple[int, int, int]]:
        position = self.seen.find(0)
        while position != -1:
            child, parent = self.index.pair(position)
            if in_scope is None or in_scope(child, parent):
                yield position, child, parent
            position = self.seen.find(0, position + 1)


class SingularCheck(_PairCheck):
    """Check (a): the stored singular fields against the rule's, edge by edge.

    Passes when every edge in scope matches: `matched == total`, and `total == expected` when `expected` is given,
    where `total` counts matched, differing, graph-only, rule-only and duplicate edges. The plural lists (`plural`,
    a `plural_shape`) and the protocol pair are counted and never decide it.
    """

    def __init__(self, index: LabelIndex, *, expected: int | None = None, example_cap: int = 20):
        super().__init__(index, example_cap)
        self.expected = expected
        self.matched = self.differing = self.graph_only = self.rule_only = 0
        self.differing_by_key = {key: 0 for key in SINGULAR_KEYS}
        self.plural_shapes: Counter = Counter()
        self.stored_protocol = self.computed_protocol = 0
        self.examples: list[dict] = []
        self._example_kinds: Counter = Counter()

    def _example(self, kind: str, child: int, parent: int, stored=None, computed=None, differs=()) -> None:
        if self._example_kinds[kind] < self.example_cap:
            self._example_kinds[kind] += 1
            self.examples.append({"kind": kind, "child": child, "parent": parent, "differs": list(differs),
                                  "stored": _subset(stored, SINGULAR_KEYS),
                                  "computed": _subset(computed, SINGULAR_KEYS)})

    def edge(self, child: int, parent: int, stored: Mapping, *, plural: str | None = None) -> None:
        """One stored edge, its ids already in the rule's id space."""
        if plural is not None:
            self.plural_shapes[plural] += 1
        if _has_protocol(stored):
            self.stored_protocol += 1
        position = self._position(child, parent)
        if position is None:
            self._example("duplicate", child, parent, stored)
            return
        if position < 0:
            self.graph_only += 1
            self._example("graph_only", child, parent, stored)
            return
        computed = self.index.labels(position)
        if _has_protocol(computed):
            self.computed_protocol += 1
        differs = singular_differences(stored, computed)
        if not differs:
            self.matched += 1
            return
        self.differing += 1
        for key in differs:
            self.differing_by_key[key] += 1
        self._example("differing", child, parent, stored, computed, differs)

    def close(self, in_scope: InScope = None) -> dict:
        """Count the declared pairs in scope that no edge matched, and return the result (once)."""
        if self._result is None:
            for position, child, parent in self._unseen(in_scope):
                self.rule_only += 1
                computed = self.index.labels(position)
                if _has_protocol(computed):
                    self.computed_protocol += 1
                self._example("rule_only", child, parent, computed=computed)
            total = self.matched + self.differing + self.graph_only + self.rule_only + self.duplicates
            passed = total > 0 and self.matched == total and (self.expected is None or total == self.expected)
            self._result = {
                "passed": passed, "expected": self.expected, "total": total, "matched": self.matched,
                "differing": self.differing, "graph_only": self.graph_only, "rule_only": self.rule_only,
                "duplicates": self.duplicates, "differing_by_key": dict(self.differing_by_key),
                "plural_shapes": dict(sorted(self.plural_shapes.items())),
                "stored_protocol": self.stored_protocol, "computed_protocol": self.computed_protocol,
                "examples": self.examples,
            }
        return self._result


class ClassCheck(_PairCheck):
    """Check (b): every stored edge sorted by `labels.classify`, counted per class, per property and per kind.

    `by_stored` splits the classes by whether the edge had a singular assay field stored; `new` splits the `new`
    edges by what the rule would write (an assay label, a protocol only, or only nulls and empty lists).
    """

    def __init__(self, index: LabelIndex, *, example_cap: int = 10):
        super().__init__(index, example_cap)
        self.classes: Counter = Counter()
        self.by_stored = {"labelled": Counter(), "unlabelled": Counter()}
        self.per_property: dict[str, dict[str, Counter]] = {}
        self.new: Counter = Counter()
        self.graph_only: Counter = Counter()
        self.rule_only: Counter = Counter()
        self.examples: dict[str, list[dict]] = {}

    def _example(self, kind: str, entry: dict) -> None:
        bucket = self.examples.setdefault(kind, [])
        if len(bucket) < self.example_cap:
            bucket.append(entry)

    def edge(self, child: int, parent: int, stored: Mapping) -> None:
        state = "labelled" if _labelled(stored) else "unlabelled"
        position = self._position(child, parent)
        if position is None:
            return
        if position < 0:
            self.graph_only[state] += 1
            self._example("graph_only", {"child": child, "parent": parent,
                                         "stored": _subset(stored, LABEL_KEYS)})
            return
        computed = self.index.labels(position)
        cls = labels.classify(stored, computed)
        self.classes[cls] += 1
        self.by_stored[state][cls] += 1
        changes = property_changes(stored, computed)
        if changes:
            per = self.per_property.setdefault(cls, {})
            for key, kind in changes.items():
                per.setdefault(key, Counter())[kind] += 1
        if cls == labels.NEW:
            self.new["with_assay" if _labelled(computed) else
                     "protocol_only" if _has_protocol(computed) else "empty"] += 1
        if cls != labels.EQUAL:
            self._example(cls, {"child": child, "parent": parent, "differs": list(changes),
                                "stored": _subset(stored, LABEL_KEYS), "computed": _subset(computed, LABEL_KEYS)})

    def close(self, in_scope: InScope = None) -> dict:
        """Count the declared pairs in scope with no edge, and return the result (once)."""
        if self._result is None:
            for position, child, parent in self._unseen(in_scope):
                computed = self.index.labels(position)
                kind = "with_assay" if _labelled(computed) else "without_assay"
                self.rule_only[kind] += 1
                self._example("rule_only", {"child": child, "parent": parent, "computed": _subset(computed, LABEL_KEYS)})
            per_property = {
                cls: {key: dict(sorted(self.per_property[cls][key].items()))
                      for key in LABEL_KEYS if key in self.per_property[cls]}
                for cls in labels.CLASSES if cls in self.per_property}
            self._result = {
                "compared": sum(self.classes.values()),
                "classes": {cls: self.classes.get(cls, 0) for cls in labels.CLASSES},
                "by_stored": {state: {cls: counts[cls] for cls in labels.CLASSES if counts.get(cls)}
                              for state, counts in self.by_stored.items()},
                "per_property": per_property,
                "new": {kind: self.new.get(kind, 0) for kind in ("with_assay", "protocol_only", "empty")},
                "graph_only": {state: self.graph_only.get(state, 0) for state in ("labelled", "unlabelled")},
                "rule_only": {kind: self.rule_only.get(kind, 0) for kind in ("with_assay", "without_assay")},
                "duplicates": self.duplicates,
                "examples": self.examples,
            }
        return self._result
