"""Resolve lab and person names in a turn against SEEK's lab records, in code.

A lab is encoded in NExtSEEK sample UIDs as a three-letter code (``TYPE-YYMMDDCODE-n``),
so a lab scope is matched by that code, never by the lab's name. SEEK records every
lab as an Institution titled ``<CODE>-<Name> Lab (<Affiliation>)``; the context export
parses those titles into ``ChatConfig.LABS`` (records ``{code, name, affiliation,
project_ids, ...}``). This module matches what the question and the entity LLM said
against those records, and emits a code only from a record it matched. A person name
that matches no lab is a ``Scientist`` value, never a guessed code.

Design: ``docs/superpowers/specs/2026-09-18-projects-labs-context.md`` section 7. The
rule ids in comments (M1 to M7, U1 to U5, E4, M5) are that section's.

Deliberately not imported here: ``chat_nextseek.labs``. The records arrive as plain
data, and anything but a list means no labs document is available.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["LabResolution", "fold", "resolve_labs"]

# ---------------------------------------------------------------------------
# 7.2 normalisation
# ---------------------------------------------------------------------------

_APOSTROPHES = {"\u2019": "'", "\u2018": "'", "\u02bc": "'"}
_APOSTROPHE_TABLE = str.maketrans(_APOSTROPHES)


def _fold_char(ch: str) -> str:
    text = unicodedata.normalize("NFKC", ch)
    text = "".join(_APOSTROPHES.get(c, c) for c in text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return text.casefold()


def _fold_map(text: str) -> tuple[str, list[int]]:
    """``fold(text)`` plus, for each folded character, the index it came from."""
    chars: list[str] = []
    index: list[int] = []
    after_space = True  # drops leading whitespace
    for i, ch in enumerate(text):
        for c in _fold_char(ch):
            if c.isspace():
                if after_space:
                    continue
                c, after_space = " ", True
            else:
                after_space = False
            chars.append(c)
            index.append(i)
    if chars and chars[-1] == " ":
        chars.pop()
        index.pop()
    return "".join(chars), index


def fold(text: str) -> str:
    """NFKC, curly apostrophes to ``'``, NFKD with combining marks dropped, casefold,
    whitespace collapsed and stripped. Equal to ``_fold_map(text)[0]``, computed on the
    whole string because the catalogs it folds for M5 are large."""
    text = unicodedata.normalize("NFKC", text or "").translate(_APOSTROPHE_TABLE)
    text = unicodedata.normalize("NFKD", text)
    if not text.isascii():
        text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


# ---------------------------------------------------------------------------
# Patterns. Names are matched in folded text; codes in the text as written.
# ---------------------------------------------------------------------------

_L = r"[^\W\d_]"  # a letter, any script
_A = r"[^\W_]"    # a letter or a digit
# A name is bounded by anything that is not a letter or digit, except that a hyphen or an
# apostrophe joined to more letters belongs to the name, so "Wren-Ashby" and "O'Brien"
# match only whole and never as "Ashby" or "Brien". A trailing possessive 's is a boundary.
_LB = rf"(?<!{_A})(?<!{_A}['-])"
_RB = rf"(?!{_A})(?!-{_A})(?!'(?!s(?!{_A})){_A})"

_HONORIFIC = r"(?:dr|prof|professor)\.?"
_FIRST = rf"{_L}(?:{_L}|['-])*\.?"  # a first name or an initial
_LAB_AFTER = r"(?:lab|labs|laboratory|group)"
_LAB_BEFORE = r"(?:lab|laboratory|group)"

_RULE_PRIORITY = {"code": 0, "lab_phrase": 1, "honorific": 2, "possessive": 3, "name": 4}

# M1: a code token as written, never part of a UID (no letter, digit or underscore beside
# it, and no ``-``/``.`` joining it to one).
_CODE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][-.])([A-Z]{3})(?![A-Za-z0-9_])(?![-.][A-Za-z0-9_])"
)
_CODE_LAB_AFTER = re.compile(r"\s+(?:lab|labs|laboratory|group)(?![A-Za-z0-9_])", re.IGNORECASE)
_CODE_LAB_BEFORE = re.compile(
    r"(?<![A-Za-z0-9_])(?:lab|laboratory|group)\s+(?:code\s+)?$", re.IGNORECASE
)

_ENTRY_LEAD = re.compile(
    r"^\s*(?:the\s+)?(?:(?:lab|laboratory|group)\s+(?:code\s+)?(?:of\s+)?)?", re.IGNORECASE
)
_ENTRY_TRAIL = re.compile(r"\s+(?:lab|labs|laboratory|group)\s*$", re.IGNORECASE)
_ENTRY_POSSESSIVE = re.compile(r"(?:['\u2019\u02bc]s|['\u2019\u02bc])$", re.IGNORECASE)
_FOLDED_HONORIFIC_LEAD = re.compile(rf"^(?:{_HONORIFIC} )+")

# U4
_NAME_TOKEN = re.compile(r"^[^\W\d_][^\W\d_'\u2019\u02bc.\-]*$")
_NOT_A_PERSON = {
    "center", "centre", "institute", "core", "facility", "university", "college",
    "hospital", "school", "department", "program", "consortium",
}
_SENTENCE_START = re.compile(r"(?:^|[.!?\n])[\s\"'(\[\u201c\u2018]*$")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Record:
    order: int
    code: str
    name: str
    affiliation: str | None
    project_ids: tuple[int, ...]
    folded_name: str


def _records(raw: list) -> list[_Record]:
    """The usable records, in the order given. A record needs a three-capital code and a
    non-empty name; anything else is skipped rather than guessed."""
    out: list[_Record] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code, name = item.get("code"), item.get("name")
        if not (isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code)):
            continue
        if not (isinstance(name, str) and name.strip() and fold(name)):
            continue
        affiliation = item.get("affiliation")
        affiliation = affiliation.strip() or None if isinstance(affiliation, str) else None
        ids = item.get("project_ids")
        project_ids = tuple(sorted({
            i for i in (ids if isinstance(ids, list) else [])
            if isinstance(i, int) and not isinstance(i, bool)
        }))
        out.append(_Record(len(out), code, name.strip(), affiliation, project_ids, fold(name)))
    return out


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------

@dataclass
class LabResolution:
    """What the entity agent emits for labs (spec 7.5), plus whether records existed."""

    available: bool
    labs: list[str] = field(default_factory=list)
    lab_codes: list[str] = field(default_factory=list)
    lab_matches: list[dict] = field(default_factory=list)
    scientists: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass
class _Hit:
    pos: int
    rule: str
    text: str
    records: list[_Record]
    ambiguous: bool


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    """Append each value not already present once folded."""
    seen = {fold(v) for v in target}
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        key = fold(value)
        if key and key not in seen:
            seen.add(key)
            target.append(value.strip())


def _strings(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [v for v in values if isinstance(v, str) and v.strip()]


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------

class _Matcher:
    def __init__(self, question: str, records: list[_Record], catalogs: Iterable[Any],
                 projects: Iterable[Any]):
        self.question = question or ""
        self.fq, self.index = _fold_map(self.question)
        self.records = records
        self.by_name: dict[str, list[_Record]] = {}
        self.by_code: dict[str, list[_Record]] = {}
        for rec in records:
            self.by_name.setdefault(rec.folded_name, []).append(rec)
            self.by_code.setdefault(rec.code, []).append(rec)
        self._catalog_rows = list(catalogs or [])
        self._catalog: tuple[set[str], str] | None = None
        self._projects = list(projects or [])
        self.question_codes = {m.group(1) for m in _CODE_TOKEN.finditer(self.question)}

    # -- spans and case ----------------------------------------------------

    def _orig(self, start: int, end: int) -> tuple[int, int]:
        """Folded span to the question's own span."""
        return self.index[start], self.index[end - 1] + 1

    def _name_occurrences(self, folded_name: str) -> list[tuple[int, int]]:
        """Every whole-word occurrence of a folded name, as spans of the question."""
        if not folded_name or folded_name not in self.fq:
            return []
        pattern = _LB + re.escape(folded_name) + _RB
        return [self._orig(m.start(), m.end()) for m in re.finditer(pattern, self.fq)]

    @staticmethod
    def _capitalised(span: str, spelled: str) -> bool:
        """``span`` carries a capital wherever SEEK's spelling starts a part with one."""
        parts_q = [p for p in re.split(r"[\s'\u2019\u02bc\-]+", span) if p]
        parts_r = [p for p in re.split(r"[\s'\u2019\u02bc\-]+", spelled) if p]
        if not any(p[:1].isupper() for p in parts_q):
            return False
        return all(q[:1].isupper() for q, r in zip(parts_q, parts_r) if r[:1].isupper())

    def _sentence_initial(self, start: int) -> bool:
        return _SENTENCE_START.search(self.question[:start]) is not None

    def _capital_evidence(self, rec_name: str, folded_name: str) -> tuple[bool, int | None]:
        """M5: a capitalised occurrence that is not merely a sentence's first word."""
        for start, end in self._name_occurrences(folded_name):
            if self._capitalised(self.question[start:end], rec_name) and not self._sentence_initial(start):
                return True, start
        return False, None

    # -- M5 ----------------------------------------------------------------

    def is_catalog_word(self, folded_name: str) -> bool:
        """M5: the name occurs as a word in the sample type or assay catalogs' names, tags
        or descriptions. Read from the catalogs themselves, so no hand-kept word list."""
        if self._catalog is None:
            texts: list[str] = []
            for row in self._catalog_rows:
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    if str(key).lower() not in ("name", "tags", "description"):
                        continue
                    if isinstance(value, str):
                        texts.append(value)
                    elif isinstance(value, (list, tuple)):
                        texts.extend(str(v) for v in value)
            text = fold(" | ".join(texts))
            words = set(re.findall(rf"{_A}+", text))
            for joined in re.findall(rf"{_A}+(?:['-]{_A}+)+", text):
                words.add(joined)
            self._catalog = (words, text)
        words, text = self._catalog
        if " " in folded_name:
            return re.search(_LB + re.escape(folded_name) + _RB, text) is not None
        return folded_name in words

    # -- M6 ----------------------------------------------------------------

    def _narrow(self, recs: list[_Record]) -> tuple[list[_Record], bool]:
        """M6: several records share the surname; keep those whose affiliation the question
        names. If it names none, keep them all and call them ambiguous."""
        if len(recs) < 2:
            return list(recs), False
        named = [
            r for r in recs
            if r.affiliation and re.search(
                rf"(?<!{_A})" + re.escape(fold(r.affiliation)) + rf"(?!{_A})", self.fq)
        ]
        if named:
            return named, False
        return list(recs), True

    # -- the question scan (M1 in the question, M2, M3) ------------------------

    def scan_question(self) -> list[_Hit]:
        found: list[tuple[int, int, str, int, int, str]] = []  # key start, prio, key, start, end, rule

        for m in _CODE_TOKEN.finditer(self.question):
            code = m.group(1)
            if code not in self.by_code:
                continue
            after = _CODE_LAB_AFTER.match(self.question, m.end())
            before = _CODE_LAB_BEFORE.search(self.question[:m.start()])
            if after:
                found.append((m.start(), 0, "code:" + code, m.start(), after.end(), "code"))
            elif before:
                found.append((m.start(), 0, "code:" + code, before.start(), m.end(), "code"))

        for folded_name, recs in self.by_name.items():
            if folded_name not in self.fq:
                continue
            spelled = recs[0].name
            n = rf"(?P<name>{re.escape(folded_name)})"
            poss = r"(?P<poss>'s|s'" + (r"|'" if folded_name.endswith("s") else "") + r")"
            patterns = (
                ("lab_phrase", rf"{_LB}{n}{poss}? (?P<word>{_LAB_AFTER})(?!{_A})"),
                ("lab_phrase", rf"(?<!{_A})(?P<word>{_LAB_BEFORE}) of (?:{_HONORIFIC} )?(?:{_FIRST} ){{0,2}}{n}{_RB}"),
                ("honorific", rf"(?<!{_A}){_HONORIFIC} (?:{_FIRST} ){{0,2}}{n}{_RB}"),
                ("possessive", rf"{_LB}{n}{poss} (?={_L})"),
            )
            for rule, pattern in patterns:
                for m in re.finditer(pattern, self.fq):
                    name_start, name_end = self._orig(m.start("name"), m.end("name"))
                    written = self.question[name_start:name_end]
                    if rule == "lab_phrase" and m.group("word") == "group" and self.is_catalog_word(folded_name):
                        # "the bone marrow group" is a sample group: for a catalog word,
                        # "group" is a lab word only when the name is written as a name.
                        if not (self._capitalised(written, spelled) and not self._sentence_initial(name_start)):
                            continue
                    if rule in ("honorific", "possessive") and not self._capitalised(written, spelled):
                        continue
                    if rule == "possessive":
                        start, end = self._orig(m.start("name"), m.end("poss"))
                    else:
                        start, end = self._orig(m.start(), m.end())
                    found.append((name_start, _RULE_PRIORITY[rule], "name:" + folded_name, start, end, rule))

        # One rule per occurrence of a name: the strongest phrase wins.
        found.sort(key=lambda f: (f[0], f[1]))
        hits: list[_Hit] = []
        seen: set[tuple[int, str]] = set()
        for key_start, _prio, key, start, end, rule in found:
            if (key_start, key) in seen:
                continue
            seen.add((key_start, key))
            kind, _, value = key.partition(":")
            if kind == "code":
                recs, ambiguous = list(self.by_code[value]), False
            else:
                recs, ambiguous = self._narrow(self.by_name[value])
            hits.append(_Hit(start, rule, self.question[start:end], recs, ambiguous))
        return hits

    # -- an LLM entry (M1 in an entry, M2 in an entry, M4) ----------------------

    @staticmethod
    def core(entry: str) -> str:
        """The entry less a leading ``the``/``lab of``, a trailing lab word and a possessive."""
        text = _ENTRY_LEAD.sub("", entry.strip(), count=1)
        text = _ENTRY_TRAIL.sub("", text).strip()
        return _ENTRY_POSSESSIVE.sub("", text).strip()

    def match_entry(self, entry: str) -> tuple[_Hit | None, bool]:
        """The hit for one LLM ``labs`` entry, and whether it named a lab but failed M5."""
        core = self.core(entry)

        # M1: the whole entry, less a lab word, is a code the question carries as a word.
        if core in self.by_code and core in self.question_codes:
            m = next(m for m in _CODE_TOKEN.finditer(self.question) if m.group(1) == core)
            return _Hit(m.start(), "code", entry.strip(), list(self.by_code[core]), False), False

        folded_entry = fold(entry)
        folded_core = fold(core)
        candidates: list[tuple[str, str]] = []  # (folded name, rule)
        for folded_name in self.by_name:
            n = re.escape(folded_name)
            poss = r"(?:'s|s'" + (r"|'" if folded_name.endswith("s") else "") + r")?"
            if (re.search(rf"{_LB}{n}{poss} {_LAB_AFTER}(?!{_A})", folded_entry)
                    or re.search(rf"(?<!{_A}){_LAB_BEFORE} of (?:{_HONORIFIC} )?(?:{_FIRST} ){{0,2}}{n}{_RB}",
                                 folded_entry)):
                candidates.append((folded_name, "lab_phrase"))
        if not candidates:
            # M4: the surname position, i.e. the part before a comma ("Last, First") or the
            # last name-token sequence; first names, initials and honorifics are ignored.
            if "," in folded_core:
                surname = _FOLDED_HONORIFIC_LEAD.sub("", folded_core.split(",", 1)[0].strip())
                names = [n for n in self.by_name if surname == n]
            else:
                bare = _FOLDED_HONORIFIC_LEAD.sub("", folded_core)
                names = [n for n in self.by_name if bare == n or bare.endswith(" " + n)]
            if names:
                candidates.append((max(names, key=len), "name"))

        failed_m5 = False
        for folded_name, rule in candidates:
            occurrences = self._name_occurrences(folded_name)
            if not occurrences:
                continue  # the LLM may not introduce a lab the user never named
            recs = self.by_name[folded_name]
            pos = occurrences[0][0]
            if self.is_catalog_word(folded_name):
                # M5: a catalog word matches through the user's own lab phrase (the question
                # scan) or a capitalised mention; the LLM's phrase alone is not evidence.
                ok, where = self._capital_evidence(recs[0].name, folded_name)
                if not ok:
                    failed_m5 = True
                    continue
                pos = where
            narrowed, ambiguous = self._narrow(recs)
            return _Hit(pos, rule, entry.strip(), narrowed, ambiguous), False
        return None, failed_m5

    # -- U1 to U5 ---------------------------------------------------------

    def only_inside_a_uid(self, code: str) -> bool:
        """The question carries ``code`` only as part of a UID-like token (M7)."""
        if code in self.question_codes:
            return False
        return re.search(r"(?<![A-Z])" + re.escape(code) + r"(?![A-Z])", self.question) is not None

    def is_project(self, entry: str) -> bool:
        keys = {fold(entry), fold(self.core(entry))}
        for row in self._projects:
            if not isinstance(row, dict):
                continue
            names = [row.get("name")]
            aliases = row.get("alternative_names")
            if isinstance(aliases, str):
                names.extend(a.strip() for a in re.split(r"[,;]", aliases))
            elif isinstance(aliases, (list, tuple)):
                names.extend(aliases)
            if any(isinstance(n, str) and fold(n) in keys for n in names):
                return True
        return False

    def person_name(self, entry: str) -> str | None:
        """U4: the entry as a person's name, spelled as the question spells it, or None."""
        name = self.core(entry)
        tokens = [t.rstrip(",") for t in name.split()]
        tokens = [t for t in tokens if t]
        if not 1 <= len(tokens) <= 4:
            return None
        if not all(_NAME_TOKEN.match(t) for t in tokens):
            return None
        if any(fold(t).strip(".") in _NOT_A_PERSON for t in tokens):
            return None
        capitalised = False
        for token in tokens:
            for start, end in self._name_occurrences(fold(token)):
                if self.question[start:start + 1].isupper():
                    capitalised = True
                    break
            if capitalised:
                break
        if not capitalised:
            return None
        spans = self._name_occurrences(fold(name))
        if spans:
            start, end = spans[0]
            return self.question[start:end]
        return name


def resolve_labs(
    question: str,
    llm_labs: Any,
    *,
    records: Any,
    llm_scientists: Any = (),
    llm_keywords: Any = (),
    catalogs: Iterable[Any] = (),
    projects: Iterable[Any] = (),
) -> LabResolution:
    """Resolve the LLM's ``labs`` and the question's lab phrases against SEEK's lab records.

    ``records`` is ``ChatConfig.LABS``: a list of lab records, or anything else when no
    labs document is available. ``catalogs`` are the sample type and assay catalog rows
    (M5); ``projects`` the projects catalog rows (U2). Returns the emitted fields; its
    ``keywords`` and ``scientists`` are the LLM's plus what this adds.
    """
    labs_in = _strings(llm_labs)
    scientists: list[str] = []
    _extend_unique(scientists, _strings(llm_scientists))
    keywords: list[str] = []
    _extend_unique(keywords, _strings(llm_keywords))

    if not isinstance(records, list):
        # Without a list "not a lab" cannot be decided, so nothing is moved.
        _extend_unique(keywords, scientists)  # E4
        return LabResolution(available=False, labs=list(labs_in), scientists=list(scientists),
                             keywords=keywords)

    matcher = _Matcher(question, _records(records), catalogs, projects)

    hits = matcher.scan_question()
    matched = {rec.order for hit in hits for rec in hit.records}
    new_scientists: list[str] = []
    new_keywords: list[str] = []
    for entry in labs_in:
        hit, failed_m5 = matcher.match_entry(entry)
        if hit is not None:
            fresh = [r for r in hit.records if r.order not in matched]
            if fresh:
                hits.append(_Hit(hit.pos, hit.rule, hit.text, fresh, hit.ambiguous))
                matched.update(r.order for r in fresh)
            continue
        core = matcher.core(entry) or entry.strip()
        if re.fullmatch(r"[A-Z]{3}", core):
            # U1: a code no lab owns; a text search still finds the UIDs carrying it. A code
            # the question holds only inside a UID is dropped instead (M7): that UID already
            # scopes the query, and a keyword would narrow it to whatever else carries it.
            if not matcher.only_inside_a_uid(core):
                new_keywords.append(core)
        elif matcher.is_project(entry):
            continue                           # U2: a project, which the LLM also lists
        elif failed_m5:
            new_keywords.append(core)          # U3: most likely the common word
        elif (person := matcher.person_name(entry)) is not None:
            new_scientists.append(person)      # U4
            new_keywords.append(person)        # E4
        else:
            new_keywords.append(core)          # U5

    labs: list[str] = []
    codes: list[str] = []
    matches: list[dict] = []
    seen: dict[tuple[str, str, str | None, str], dict] = {}
    for hit in sorted(hits, key=lambda h: (h.pos, _RULE_PRIORITY[h.rule])):
        for rec in sorted(hit.records, key=lambda r: r.order):
            if rec.name not in labs:
                labs.append(rec.name)
            if rec.code not in codes:
                codes.append(rec.code)
            key = (rec.code, rec.name, rec.affiliation, hit.text)
            if key in seen:
                merged = sorted(set(seen[key]["project_ids"]) | set(rec.project_ids))
                seen[key]["project_ids"] = merged
                continue
            match = {
                "text": hit.text, "code": rec.code, "name": rec.name,
                "affiliation": rec.affiliation, "project_ids": list(rec.project_ids),
                "rule": hit.rule, "ambiguous": hit.ambiguous,
            }
            seen[key] = match
            matches.append(match)

    _extend_unique(scientists, new_scientists)
    _extend_unique(keywords, new_keywords)
    _extend_unique(keywords, scientists)  # E4
    return LabResolution(available=True, labs=labs, lab_codes=codes, lab_matches=matches,
                         scientists=scientists, keywords=keywords)


def lab_code(name: str | None) -> str:
    """Return the first 3 alphabetic characters of ``name``, uppercased.

    Retired by the matcher above; removed once its one caller moves to it."""
    if not isinstance(name, str):
        return ""
    alpha = "".join(ch for ch in name if ch.isalpha())
    return alpha[:3].upper() if len(alpha) >= 3 else ""
