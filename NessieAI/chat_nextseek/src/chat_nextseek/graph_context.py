"""Render the graph agent's schema context, variant (b), from the v1.1 catalog.

Pure functions: no Neo4j, no config, no Django. The catalog reader (``graph_catalog``) supplies the rows; this
module only turns them into compact text. The rows are read by field name, so any object with the fields of
``graph_catalog``'s dataclasses (or a dict with the same keys) renders.

The text has three parts (spec section 4.2):

1. the structure, hand-owned text in ``prompts/graph_schema_structure.txt``, kept consistent with
   ``docs/neo4j-schema.md`` v1.1 by a test;
2. the type index, one line per non-deprecated sample type;
3. at most ``MAX_TYPES`` resolved types, each with its K most-filled attributes in full and the rest by name,
   each with its sample count.

``render_graph_context`` holds the whole text within ``BUDGET_BYTES``: when it is over, the tail's counts go,
then K steps down through ``K_STEPS`` (0 means names only), before a resolved section is dropped, the last one
first. ``fit_graph_context`` returns the same text with what it gave up, and prints any step down.

The vocabulary, a separate message, is gated by question words (``mentions``) and held within
``VOCAB_BUDGET_BYTES`` by ``fit_vocabulary``, which trims the entries the question does not name first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

STRUCTURE_PATH: Path = Path(__file__).resolve().parent / "prompts" / "graph_schema_structure.txt"
BUDGET_BYTES = 32_768
K_STEPS = (25, 15, 10, 0)  # 0 means names only
# The vocabulary blocks' own bound (render_vocabulary, and the committed-files path in agents/graph.py). Set from
# a replay of the evaluation questions against a vocabulary the size of the live one: the largest rendering of
# a question firing one or two gated groups sits just under 28 KiB, so every such question renders whole, and
# what the bound trims today is a question firing all three groups at once. It trims what the question does not
# name, never what it does (fit_vocabulary).
VOCAB_BUDGET_BYTES = 28_672
MAX_TYPES, MEANING_MAX = 3, 120
SUMMARY_MAX = 240  # a summary's first sentence is cut here, so one long summary cannot outgrow the budget

# The keyword gates of the protocol and assay blocks, on both the catalog path and the committed-files path
# (agents/graph.py), matched by ``mentions``. "dataset" is here because "data" no longer fires inside "datasets".
PROTOCOL_WORDS = ("protocol", "sop", "method", "procedure", "technique")
ASSAY_WORDS = ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "dataset", "processed",
               "associated", "underwent", "via", "collection", "extraction")
# Studies and published studies: study, paper, publication (and publish), DOI, PMID, as whole words.
STUDY_WORDS_RE = re.compile(r"\b(?:stud(?:y|ies)|papers?|publications?|publish\w*|doi|pmid)\b", re.IGNORECASE)

# v1.1 rule 1: the metadata key UID is never written as a property (it equals uuid).
_SKIPPED_ATTRIBUTES = frozenset({"UID"})
_ABBREVIATIONS = frozenset({"e.g", "i.e", "etc", "vs", "approx", "cf", "ca", "no", "fig", "resp", "incl", "esp"})
_STOP_RE = re.compile(r"[.;!?](?=\s|$)")
_KEY_PREFIX_RE = re.compile(r"^\d+:")
_BLOCK_JOIN = "\n\n"
_WORD_RE = re.compile(r"[a-z0-9]+")
# Words that name no vocabulary entry: the English glue of a question, and the words nearly every question
# uses to ask for a block at all (sample, data, study, assay, protocol and the like).
_PLAIN_WORDS = frozenset("""
    about across after all also amount and any are available been before being between both but can could count
    did does doing done each either every exist exists find first for from get give had has have having her here
    his how into its just last like list look made make making many may might more most much must need not number
    only other our over per see shall she should show some such tell than that the their them then there these
    they this those too total under use used using very want was were what when where whether which while who
    whom whose why will with within without would you your
    assay data database dataset investigation method procedure project protocol publication published sample sop
    study studies technique type kind associated underwent processed paper
""".split())


def _gate_pattern(words: tuple[str, ...]) -> re.Pattern:
    alternation = "|".join(re.escape(word) for word in words)
    return re.compile(rf"\b(?:re|sub)?(?:{alternation})(?:s|es|ed|ing)?\b", re.IGNORECASE)


# The two lists every turn uses, compiled once. Any other list is compiled per call, so nothing grows here.
_KNOWN_GATES = {PROTOCOL_WORDS: _gate_pattern(PROTOCOL_WORDS), ASSAY_WORDS: _gate_pattern(ASSAY_WORDS)}


def mentions(words, text: Any) -> bool:
    """True when ``text`` uses one of ``words`` as a whole word, a gate word never matching inside another word.

    A substring test fired "data" inside "database" and "via" inside "trivial". The word may carry an
    inflection (-s, -es, -ed, -ing: "assays", "assayed") or a re- or sub- prefix ("resequencing", "subassays"),
    which the substring test also caught. An empty list or an empty text matches nothing.
    """
    words = tuple(words or ())
    if not words or not text:
        return False
    gate = _KNOWN_GATES.get(words) or _gate_pattern(words)
    return bool(gate.search(str(text)))


# ---------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    """A field of a dataclass, a SimpleNamespace or a dict, or ``default``."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump() or {}
    return dict(vars(obj)) if hasattr(obj, "__dict__") else {}


def _count(n: int) -> str:
    return f"{int(n):,}"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_count(n)} {singular if int(n) == 1 else plural}"


def _samples(n: int | None) -> str | None:
    if n is None:
        return None
    if int(n) == 0:
        return "no samples"
    return _plural(n, "sample", "samples")


def _quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _number(x: Any) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.6g}"


def _property_name(title: str, needs_backticks: bool) -> str:
    return "`" + title.replace("`", "``") + "`" if needs_backticks else title


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit - 3]
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space]
    return head.rstrip(" ,;:") + "..."


def _first_stop(text: str) -> re.Match | None:
    """The first sentence or clause end, skipping abbreviations (``e.g.``) and initials."""
    for match in _STOP_RE.finditer(text):
        if match.group() == ".":
            word = text[:match.start()].rsplit(" ", 1)[-1].lower()
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
        return match
    return None


def first_clause(text: Any, limit: int = MEANING_MAX) -> str | None:
    """An attribute meaning trimmed to its first clause (up to ``.`` or ``;``), at most ``limit`` characters."""
    if text is None:
        return None
    s = _one_line(text)
    if not s:
        return None
    stop = _first_stop(s)
    if stop is not None:
        s = s[:stop.start()].rstrip()
    return _cut(s, limit) if s else None


def first_sentence(text: Any, limit: int = SUMMARY_MAX) -> str | None:
    """A summary's first sentence, its full stop kept, at most ``limit`` characters."""
    if text is None:
        return None
    s = _one_line(text)
    if not s:
        return None
    stop = _first_stop(s)
    if stop is not None:
        s = s[:stop.end()] if stop.group() != ";" else s[:stop.start()]
    return _cut(s, limit)


# ---------------------------------------------------------------------------------------------------------------
# Resolved codes
# ---------------------------------------------------------------------------------------------------------------

def _item_code(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    code = _get(item, "code")
    return code if isinstance(code, str) else None


def resolved_type_codes(plan: dict | None, entity: dict | None, known: set[str]) -> list[str]:
    """The sample type codes whose sections the graph agent gets, at most ``MAX_TYPES``, in order.

    The parser plan's ``resolved.sampletypes`` and ``filters.sampletype_code`` come first, then the entity
    output's ``sampletypes``. Codes the catalog does not know are dropped, and each code is kept once. A code that
    differs from a known title only in case maps onto it when that title is the only such match.
    """
    known = set(known or ())
    by_fold: dict[str, list[str]] = {}
    for title in known:
        by_fold.setdefault(title.casefold(), []).append(title)

    def resolve(raw: str | None) -> str | None:
        if not raw:
            return None
        raw = raw.strip()
        if raw in known:
            return raw
        folded = by_fold.get(raw.casefold(), [])
        return folded[0] if len(folded) == 1 else None

    plan_d, entity_d = _as_dict(plan), _as_dict(entity)
    candidates: list[str | None] = []
    candidates += [_item_code(item) for item in (_as_dict(plan_d.get("resolved")).get("sampletypes") or [])]
    filter_code = _as_dict(plan_d.get("filters")).get("sampletype_code")
    if isinstance(filter_code, str) and filter_code.strip():
        if resolve(filter_code) is not None:
            candidates.append(filter_code)
        else:
            candidates += re.split(r"[,;|\s]+", filter_code)
    candidates += [_item_code(item) for item in (entity_d.get("sampletypes") or [])]

    codes: list[str] = []
    for raw in candidates:
        code = resolve(raw)
        if code is not None and code not in codes:
            codes.append(code)
        if len(codes) == MAX_TYPES:
            break
    return codes


# ---------------------------------------------------------------------------------------------------------------
# The type index and the per-type sections
# ---------------------------------------------------------------------------------------------------------------

def _index_line(row: Any) -> str:
    head = f"{_get(row, 'title')} :{_get(row, 'label')}"
    if _get(row, "name"):
        head += " " + _quote(_get(row, "name"))
    if _get(row, "clade"):
        head += f" clade {_get(row, 'clade')}"
    samples = _samples(_get(row, "sample_count"))
    parts = [head, samples if samples is not None else "sample count unknown",
             _plural(_get(row, "attributes_with_values") or 0, "attribute with values", "attributes with values")]
    return ", ".join(parts)


def render_type_index(rows) -> str:
    """One line per non-deprecated sample type, in the order given, under a heading."""
    lines = ["## Sample types (code :label \"name\" clade, samples, attributes with values)"]
    lines += [_index_line(row) for row in rows or () if not _get(row, "deprecated", False)]
    return "\n".join(lines)


def _filled(attributes) -> list:
    """Attributes that hold a value, most-filled first (an unknown count after the known ones), then by title."""
    rows = [a for a in attributes or ()
            if _get(a, "title") not in _SKIPPED_ATTRIBUTES and _get(a, "sample_count") != 0]
    return sorted(rows, key=lambda a: (_get(a, "sample_count") is None, -(_get(a, "sample_count") or 0),
                                       str(_get(a, "title"))))


def _range(low: Any, high: Any, fmt) -> str | None:
    if low is None and high is None:
        return None
    return f"range {fmt(low) if low is not None else '?'}..{fmt(high) if high is not None else '?'}"


def _attribute_line(attribute: Any) -> str:
    title = str(_get(attribute, "title"))
    head = f"- {_property_name(title, bool(_get(attribute, 'needs_backticks')))} [{_get(attribute, 'value_type')}]"
    if _get(attribute, "sample_count") is not None:
        head += f" n={_count(_get(attribute, 'sample_count'))}"
    if _get(attribute, "declared") is False:
        head += " (undeclared)"
    parts = [head]
    unit_key = _get(attribute, "unit_key")
    if unit_key:
        parts.append(f"unit of {_KEY_PREFIX_RE.sub('', str(unit_key))}")
    for piece in (_range(_get(attribute, "num_min"), _get(attribute, "num_max"), _number),
                  _range(_get(attribute, "date_min"), _get(attribute, "date_max"), str),
                  first_clause(_get(attribute, "meaning"))):
        if piece:
            parts.append(piece)
    return " | ".join(parts)


def _tail_entry(attribute: Any, with_count: bool) -> str:
    """A names-only entry: the property name, then ``n=`` the samples holding a value when the catalog knows it.

    ``_filled`` orders attributes by fill, so the names-only tail holds a type's sparsest attributes. A bare name
    there does not say that only some of the type's samples hold a value, so a filter on it that returns nothing
    can be reported as a finding; the count says how many samples it could have matched at most.
    """
    name = _property_name(str(_get(attribute, "title")), bool(_get(attribute, "needs_backticks")))
    count = _get(attribute, "sample_count")
    return f"{name} n={_count(count)}" if with_count and count is not None else name


def render_type_section(detail, k: int, *, tail_counts: bool = True) -> str:
    """One resolved type: header, summary sentence, curated lines, K attributes in full, the rest by name (with
    each one's sample count unless ``tail_counts`` is False)."""
    head = f"### {_get(detail, 'title')} :{_get(detail, 'label')}"
    if _get(detail, "name"):
        head += " " + _quote(_get(detail, "name"))
    parts = [head]
    if _get(detail, "clade"):
        parts.append(f"clade {_get(detail, 'clade')}")
    samples = _samples(_get(detail, "sample_count"))
    if samples is not None:
        parts.append(samples)
    lines = [", ".join(parts)]

    summary = first_sentence(_get(detail, "summary"))
    if summary:
        lines.append(summary)
    for label, field in (("Curated parents", "curated_parents"), ("Curated children", "curated_children")):
        value = _get(detail, field)
        if value:
            lines.append(f"{label}: {_one_line(value)}")

    filled = _filled(_get(detail, "attributes"))
    k = max(int(k), 0)
    lines += [_attribute_line(a) for a in filled[:k]]
    rest = filled[k:]
    if rest:
        names = ", ".join(_tail_entry(a, tail_counts) for a in rest)
        lines.append(f"{'also filled' if k else 'filled'}: {names}")
    if not filled:
        lines.append("(no attribute holds a value)")
    never = int(_get(detail, "never_filled") or 0)
    if never:
        lines.append(f"{_plural(never, 'declared attribute holds', 'declared attributes hold')} no value")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# The whole context
# ---------------------------------------------------------------------------------------------------------------

def load_structure() -> str:
    """The hand-owned structure section, without trailing blank lines."""
    return STRUCTURE_PATH.read_text(encoding="utf-8").strip()


def _assemble(structure: str, index: str, titles: list[str], sections: list[str], k: int, tail_counts: bool,
              omitted: list[str]) -> str:
    parts = [structure, index]
    if sections:
        with_n = " with n" if tail_counts else ""
        if k:
            how = f"the {k} most-filled attributes in full, then the rest by name{with_n}"
        else:
            how = "attribute names only" + ("," + with_n if with_n else "")
        parts.append(
            f"## Resolved sample types: {', '.join(titles)} ({how}; per attribute: [value type] n=samples "
            "holding a value | range | meaning)")
        parts += sections
    if omitted:
        parts.append(f"Left out to fit the context budget: {', '.join(omitted)} (see the type index).")
    return "\n\n".join(parts) + "\n"


def _fits(text: str, budget: int) -> bool:
    return len(text.encode("utf-8")) <= budget


class GraphContext(NamedTuple):
    """What ``fit_graph_context`` sent and how it fit, for a caller or a debug payload to record."""

    text: str
    requested_k: int
    k: int  # the K the resolved sections were rendered at; 0 means names only
    tail_counts: bool  # whether the names-only tail carries each attribute's sample count
    omitted: tuple[str, ...]  # resolved types left out to fit, in order
    budget: int

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8"))

    @property
    def stepped_down(self) -> bool:
        """Whether the text gave anything up to fit: the tail counts, a K step or a section."""
        return self.k < self.requested_k or not self.tail_counts or bool(self.omitted)


def _reported(fit: GraphContext) -> GraphContext:
    if fit.stepped_down:
        gave_up = [f"K {fit.requested_k} -> {fit.k}"]
        if not fit.tail_counts:
            gave_up.append("tail counts off")
        if fit.omitted:
            gave_up.append("left out " + ", ".join(fit.omitted))
        print(f"[DEBUG][GRAPH] Schema context stepped down to fit {_count(fit.budget)} bytes: "
              f"{'; '.join(gave_up)} ({_count(fit.size)} bytes)")
    return fit


def fit_graph_context(snapshot, details, *, k: int = 25, budget: int = BUDGET_BYTES,
                      structure: str | None = None) -> GraphContext:
    """Structure, type index and at most ``MAX_TYPES`` resolved sections within ``budget`` bytes, and how they fit.

    Over the budget, the sections give things up in this order, for every section at once, stopping at the
    first rendering that fits: the sample counts on the names-only tail, then a K step (``k``, then each smaller
    step of ``K_STEPS``, 0 meaning names only) with the counts back on, and so on down; only at names only are
    sections dropped, the last one first. The structure and the index are always sent, so the text exceeds the
    budget only when they alone do. Anything given up is in the result (``stepped_down``) and printed.

    ``structure`` replaces the hand-owned structure file for one call: an evaluation prompt variant's
    ``graph_schema_structure.txt`` (``prompt_variants.py``). None, the default, reads the file.
    """
    structure = load_structure() if structure is None else structure
    index = render_type_index(_get(snapshot, "index") or ())
    details = list(details or ())[:MAX_TYPES]
    titles = [str(_get(d, "title")) for d in details]
    requested = max(int(k), 0)
    if not details:
        return GraphContext(_assemble(structure, index, [], [], requested, True, []), requested, requested, True,
                            (), budget)

    steps = [requested] + [step for step in K_STEPS if step < requested]
    text = ""
    for step in steps:
        for counts in (True, False):
            text = _assemble(structure, index, titles,
                             [render_type_section(d, step, tail_counts=counts) for d in details], step, counts, [])
            if _fits(text, budget):
                return _reported(GraphContext(text, requested, step, counts, (), budget))

    last_step = steps[-1]
    kept = list(details)
    omitted: list[str] = []
    while kept:
        omitted.insert(0, str(_get(kept.pop(), "title")))
        for counts in (True, False):
            text = _assemble(structure, index, [str(_get(d, "title")) for d in kept],
                             [render_type_section(d, last_step, tail_counts=counts) for d in kept], last_step,
                             counts, omitted)
            if _fits(text, budget):
                return _reported(GraphContext(text, requested, last_step, counts, tuple(omitted), budget))
    return _reported(GraphContext(text, requested, last_step, False, tuple(omitted), budget))


def render_graph_context(snapshot, details, *, k: int = 25, budget: int = BUDGET_BYTES,
                         structure: str | None = None) -> str:
    """The text of ``fit_graph_context``: structure, type index and the resolved sections within ``budget``."""
    return fit_graph_context(snapshot, details, k=k, budget=budget, structure=structure).text


# ---------------------------------------------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------------------------------------------

class VocabularyBlock(NamedTuple):
    """One vocabulary block as parts, so a trim can keep some entries and still say what it left out.

    Whole, it renders ``head + sep.join(entries) + tail``. ``keys`` is the text each entry is matched against the
    question on (the entries themselves when empty).
    """

    head: str  # the heading, and whatever opens the list
    entries: tuple[str, ...]  # rendered, in list order
    sep: str  # between two entries
    tail: str = ""  # closes the list (a JSON list's bracket)
    keys: tuple[str, ...] = ()

    def render(self) -> str:
        return self.head + self.sep.join(self.entries) + self.tail


def json_list_block(heading: str, items) -> VocabularyBlock | None:
    """``heading`` over ``json.dumps(items, indent=2)`` as a block, byte for byte when nothing is trimmed."""
    items = list(items or ())
    if not items:
        return None
    entries = tuple("\n".join("  " + line for line in json.dumps(item, indent=2).splitlines()) for item in items)
    keys = tuple(" ".join(str(v) for v in item.values()) if isinstance(item, dict) else str(item) for item in items)
    return VocabularyBlock(heading + "\n[\n", entries, ",\n", "\n]", keys)


def question_words(question: Any) -> frozenset[str]:
    """The words of a question that can name a vocabulary entry: lower case, a plural -s cut, at least three
    characters, and none of ``_PLAIN_WORDS``."""
    words = set()
    for raw in _WORD_RE.findall(str(question or "").lower()):
        stem = _stem(raw)
        if len(stem) >= 3 and raw not in _PLAIN_WORDS and stem not in _PLAIN_WORDS:
            words.add(stem)
    return frozenset(words)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _uses(word: str, token: str) -> bool:
    """Whether an entry's token uses a question word: one stem, one five-letter root ("collected" and
    "collection"), or, for a word of four letters or more, the word inside the token, since a filename runs its
    words together ("3DOpticalFlowAlgorithm"). Keeping a spare entry costs bytes; dropping a named one costs the
    match the agent is told to make."""
    return word == token or (len(word) >= 5 and len(token) >= 5 and word[:5] == token[:5]) or (
        len(word) >= 4 and word in token)


def _named_by(key: str, words: frozenset[str]) -> int:
    """How many of the question's words an entry uses; 0 means the question does not name it."""
    if not words:
        return 0
    stems = {_stem(token) for token in _WORD_RE.findall(key.lower())}
    return sum(1 for word in words if any(_uses(word, stem) for stem in stems))


def _left_out_note(left_out: int, named: int) -> str:
    if not left_out:
        return ""
    if not named:
        return f"\n(and {_count(left_out)} more not shown here; none of them shares a word with the question)"
    return f"\n(and {_count(left_out)} more not shown here, {_count(named)} of them sharing a word with the question)"


class _Trim:
    """A block being trimmed: the entries it keeps, and its size in bytes, kept by arithmetic."""

    def __init__(self, block: VocabularyBlock, words: frozenset[str]):
        self.block = block
        self.scores = [_named_by(key, words) for key in (block.keys or block.entries)]
        self.sizes = [len(entry.encode("utf-8")) for entry in block.entries]
        self.kept = list(range(len(block.entries)))
        self.kept_bytes = sum(self.sizes)
        self.named_out = 0
        self.fixed = len(block.head.encode("utf-8")) + len(block.tail.encode("utf-8"))
        self.sep = len(block.sep.encode("utf-8"))

    def note(self) -> str:
        return _left_out_note(len(self.block.entries) - len(self.kept), self.named_out)

    @property
    def bytes(self) -> int:
        return self.fixed + self.kept_bytes + self.sep * (len(self.kept) - 1) + len(self.note().encode("utf-8"))

    def unnamed_victim(self) -> int | None:
        """The last kept entry the question does not name, while the block keeps another entry."""
        if len(self.kept) > 1:
            for i in reversed(self.kept):
                if not self.scores[i]:
                    return i
        return None

    def named_victim(self) -> int | None:
        """The kept entry using the fewest of the question's words (the later on a tie), leaving one entry."""
        if len(self.kept) > 1:
            return min(self.kept, key=lambda i: (self.scores[i], -i))
        return None

    def drop(self, i: int) -> None:
        self.kept.remove(i)
        self.kept_bytes -= self.sizes[i]
        self.named_out += bool(self.scores[i])

    def render(self) -> str:
        block = self.block
        return block.head + block.sep.join(block.entries[i] for i in self.kept) + block.tail + self.note()


def fit_vocabulary(blocks, question: str, *, budget: int = VOCAB_BUDGET_BYTES) -> list[str]:
    """The blocks as text, their blank-line join within ``budget`` bytes, keeping what the question names.

    Under budget every block renders whole. Over it, the largest block loses its last entry that shares no word
    with the question (``question_words``), again and again, so a block is cut only once it is the largest. Only
    when every block is down to entries the question names (or to one entry) do those go, the ones using the
    fewest of its words first. Each block keeps at least one entry; after that whole blocks go, the last first.
    A trimmed block ends on a line counting what it left out, and whether any of that shares a word with the
    question.
    """
    blocks = [block for block in blocks or () if block is not None and block.entries]
    whole = [block.render() for block in blocks]
    if _fits(_BLOCK_JOIN.join(whole), budget):
        return whole

    words = question_words(question)
    trims = [_Trim(block, words) for block in blocks]

    def total() -> int:
        return sum(trim.bytes for trim in trims) + len(_BLOCK_JOIN) * max(len(trims) - 1, 0)

    for victim in (_Trim.unnamed_victim, _Trim.named_victim):
        while total() > budget:
            candidates = [trim for trim in trims if victim(trim) is not None]
            if not candidates:
                break
            largest = max(candidates, key=lambda trim: trim.bytes)
            largest.drop(victim(largest))
    while trims and total() > budget:
        trims.pop()
    return [trim.render() for trim in trims]


def _title_block(heading: str, titles) -> VocabularyBlock | None:
    titles = [t for t in titles or () if t not in (None, "")]
    if not titles:
        return None
    return VocabularyBlock(f"{heading}:\n", tuple(_quote(t) for t in titles), ", ", "", tuple(map(str, titles)))


def _field_ci(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        for key, value in record.items():
            if str(key).lower() == name.lower():
                return value
        return None
    return _get(record, name) or _get(record, name.lower())


def _published_block(studies) -> VocabularyBlock | None:
    lines = []
    for study in studies or ():
        title = _field_ci(study, "title")
        bits = [_quote(title) if title else "(untitled)"]
        for field in ("DOI", "PMID"):
            value = _field_ci(study, field)
            if value not in (None, ""):
                bits.append(f"{field} {value}")
        lines.append("- " + ", ".join(bits))
    if not lines:
        return None
    return VocabularyBlock("PUBLISHED STUDIES (Study nodes with a DOI or PMID):\n", tuple(lines), "\n")


def _connections_block(connections) -> VocabularyBlock | None:
    grouped: dict[str, list[str]] = {}
    for conn in connections or ():
        assay = _get(conn, "assay")
        parent, child = _get(conn, "parent_type"), _get(conn, "child_type")
        if assay is None or parent is None or child is None:
            grouped.setdefault("", []).append(json.dumps(conn if isinstance(conn, dict) else _as_dict(conn),
                                                         ensure_ascii=False, sort_keys=True))
            continue
        pair = f"{parent} -> {child}"
        grouped.setdefault(str(assay), [])
        if pair not in grouped[str(assay)]:
            grouped[str(assay)].append(pair)
    if not grouped:
        return None
    lines = [f"- {_quote(assay)}: {', '.join(pairs)}" if assay else f"- {', '.join(pairs)}"
             for assay, pairs in grouped.items()]
    return VocabularyBlock("ASSAY-SAMPLE CONNECTIONS (assay: parent type -> child type; shows which side of an assay "
                           "a sample type sits on):\n", tuple(lines), "\n")


def render_vocabulary(vocab, question: str, *, budget: int = VOCAB_BUDGET_BYTES) -> str:
    """The keyword-gated vocabulary blocks, blank-line separated ("" when there is nothing to send).

    Investigation and project titles always; study titles and published studies when the question names a
    study, paper, publication, DOI or PMID; assay titles and assay connections on ``ASSAY_WORDS``; protocol
    titles on ``PROTOCOL_WORDS`` (both matched as whole words by ``mentions``). The text is held within
    ``budget`` bytes by ``fit_vocabulary``, which never cuts an entry sharing a word with the question while
    one that does not is still sent.
    """
    q = question or ""
    blocks = [
        _title_block("INVESTIGATION TITLES (Investigation.title)", _get(vocab, "investigation_titles")),
        _title_block("PROJECT TITLES (Project.title)", _get(vocab, "project_titles")),
    ]
    if STUDY_WORDS_RE.search(q):
        blocks.append(_title_block("STUDY TITLES (Study.title)", _get(vocab, "study_titles")))
        blocks.append(_published_block(_get(vocab, "published_studies")))
    if mentions(ASSAY_WORDS, q):
        blocks.append(_title_block("ASSAY TITLES (DERIVED_FROM.internal_assay_title values)",
                                   _get(vocab, "assay_titles")))
        blocks.append(_connections_block(_get(vocab, "assay_connections")))
    if mentions(PROTOCOL_WORDS, q):
        blocks.append(_title_block("PROTOCOL TITLES (DERIVED_FROM.protocol_title values)",
                                   _get(vocab, "protocol_titles")))
    return _BLOCK_JOIN.join(fit_vocabulary(blocks, q, budget=budget))


def _fold_title(text: Any) -> str:
    """Case and every character outside [a-z0-9] dropped: "MIT SRP", "MIT_SRP" and "mit-srp" are one name."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def project_titles_for(names, project_rows, titles) -> dict[str, str]:
    """Each project name the entity step resolved, mapped to the ``Project.title`` it is stored under.

    The entity step resolves a project to the projects catalog's name ("Impact"), and the graph stores the SEEK
    title ("IMPAcTb"). A name is mapped when it folds to one of ``titles`` itself, or when it is the name or an
    alternative name of exactly one catalog PROJECT row (``project_rows``, which the caller has already cut to
    project rows) whose own names fold to exactly one of ``titles``. Anything ambiguous, and any name that reaches
    no title in ``titles``, is left out: this only ever names a title the graph has and the caller can see.
    """
    by_fold: dict[str, set[str]] = {}
    for title in titles or ():
        if isinstance(title, str) and title.strip():
            by_fold.setdefault(_fold_title(title), set()).add(title)
    out: dict[str, str] = {}
    for name in names or ():
        if not isinstance(name, str) or not name.strip() or name in out:
            continue
        key = _fold_title(name)
        direct = by_fold.get(key, set())
        if len(direct) == 1:
            out[name] = next(iter(direct))
            continue
        found: set[str] = set()
        for row in project_rows or ():
            if not isinstance(row, dict):
                continue
            row_names = {_fold_title(n) for n in [row.get("name"), *(row.get("alternative_names") or [])]
                         if isinstance(n, str) and n.strip()}
            if key in row_names:
                for folded in row_names:
                    found |= by_fold.get(folded, set())
        if len(found) == 1:
            out[name] = next(iter(found))
    return out


def render_project_titles(mapping: dict[str, str]) -> str:
    """The block telling the graph agent which ``Project.title`` each resolved project is ("" for none)."""
    if not mapping:
        return ""
    lines = [f"- {_quote(name)} is the project titled {_quote(title)}" for name, title in mapping.items()]
    return ("PROJECTS NAMED IN THIS QUESTION (the exact Project.title each is stored under, found through the "
            "project catalog's names and alternative names; scope on this title, STEP 5):\n" + "\n".join(lines))
