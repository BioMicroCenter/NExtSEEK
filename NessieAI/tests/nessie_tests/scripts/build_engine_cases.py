"""Truth skeletons and `--cases` files for the graph_search Nessie POC (spec E4, section 5).

Three modes, all on the host and all free:

    # a truth skeleton for one family's kept questions (plan tasks G1 to G7)
    build_engine_cases.py --skeleton --selection "$GS_WORK/nessie/selection.json" \\
        --group a|b --family <f> [--part 1|2] --out "$GS_WORK/nessie/truth/<file>.json"
    # the same for the ladder or B2 question files (plan task G3, G7)
    build_engine_cases.py --questions <file> --source ladder|b2 --group a|b --out <file>
    # the cases files the arms run from (plan task G8)
    build_engine_cases.py --cases --group a|b --truth "$GS_WORK/nessie/truth" \\
        --out-dir "$GS_WORK/nessie/cases" --pilot 10|8

A skeleton copies each question's turn verbatim, with no oracle yet; `corpus_numbers` keeps
the numbers the corpus's old reply regexes expected, for the summary. `--cases` writes
`group-<g>.json` (every scorable question not merged into another), `pilot-<g>.json` and
`rest-<g>.json`: catalog-shaped, one block per original family, one inline variant per
question, engine-neutral criteria only, the broad_match questions last. Every file written
here is mode 600 in a mode 700 directory: the questions can name real people (spec E4).

Run from the repository root: `uv run --no-project --with pydantic python -m
NessieAI.tests.nessie_tests.scripts.build_engine_cases ...`.
"""
from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import math
import os
import re
import sys
from pathlib import Path

from NessieAI.tests.nessie_tests import corpus, engine_truth as et

GROUPS = {"a": "A", "b": "B"}
OTHER = "other"
# Group B's two big families have their own truth files; `--family other` takes the rest.
B_MAIN_FAMILIES = ("graph_traversal", "lineage_tree")
ENGINE_COMPARE_TAG = "engine_compare"
# The only criteria a cases file carries: they judge both arms by the same words. The
# arm-specific stages (the Cypher, the API body, the engine's value) are the scorer's.
ENGINE_NEUTRAL = frozenset({("last_reply", "matches_re"), ("entity_sampletype_codes", "contains"),
                            ("outcome_observed", "true")})
# selection.json marks the 31 lineage questions that route to REST today with rule (d).
_REST_ROUTED_RULE = re.compile(r"^\s*rule d\b", re.IGNORECASE)
_ENDPOINT_NAME = re.compile(r"[A-Za-z_-]+")
_SOURCE_RANK = {"corpus": 0, "ladder": 1, "b2": 2}


# ── the old reply numbers ────────────────────────────────────────────────────

_NUM_TOKEN = re.compile(r"\d(?:\d|,\??(?=\d)|\[\d+\])*")
_MAX_EXPANSION = 4


def regex_numbers(regex: str) -> list[float]:
    """The numbers a corpus reply regex expects, for the truth summary (not for scoring).

    `\\b195\\b` -> [195]; `3,?061` -> [3061]; `\\b52[59]\\b` -> [525, 529]; an
    alternation gives each branch. A regex with a negative lookaround says what must NOT
    appear, so it gives nothing; so does a token that continues into a range class
    (`50,?88[0-9]`) or expands to more than four numbers.
    """
    if "(?!" in regex or "(?<!" in regex:
        return []
    text = re.sub(r"\{\d*,?\d*\}", "", regex)
    text = re.sub(r"\(\?[a-zA-Z]+\)", "", text)
    text = re.sub(r"\\[A-Za-z]", " ", text)
    out: list[float] = []
    for match in _NUM_TOKEN.finditer(text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if (before and before in "[-") or after == "[":
            continue
        parts = re.findall(r"\d|\[\d+\]", match.group(0))
        choices = [list(p[1:-1]) if p.startswith("[") else [p] for p in parts]
        if math.prod(len(c) for c in choices) > _MAX_EXPANSION:
            continue
        for combo in itertools.product(*choices):
            value = float("".join(combo))
            if value not in out:
                out.append(value)
    return out


def _corpus_numbers(turn) -> list[float]:
    out: list[float] = []
    for c in turn.pass_criteria:
        if c.field != "last_reply" or not isinstance(c.value, str):
            continue
        if c.op == "matches_re":
            found = regex_numbers(c.value)
        elif c.op == "mentions" and re.fullmatch(r"\d[\d,]*(?:\.\d+)?", c.value.strip()):
            found = [float(c.value.replace(",", ""))]
        else:
            found = []
        out += [n for n in found if n not in out]
    return out


# ── skeletons ────────────────────────────────────────────────────────────────

def _group(value: str) -> str:
    try:
        return GROUPS[value.lower()]
    except KeyError:
        raise ValueError(f"the group is a or b, not {value!r}") from None


def _halves(entries: list, part) -> list:
    if part is None:
        return entries
    cut = math.ceil(len(entries) / 2)
    if int(part) == 1:
        return entries[:cut]
    if int(part) == 2:
        return entries[cut:]
    raise ValueError(f"--part is 1 or 2, not {part!r}")


def _selected(selection: dict, group: str, family: str) -> list[dict]:
    if group == "A":
        families = selection.get("families") or {}
        if family == OTHER or family not in families:
            raise ValueError(f"Group A has no kept family {family!r} in the selection "
                             f"(it has {sorted(families)})")
        return [dict(e, family=family) for e in families[family].get("kept") or []]
    kept = (selection.get("group_b") or {}).get("kept") or []
    if family == OTHER:
        entries = [e for e in kept if e.get("family") not in B_MAIN_FAMILIES]
    else:
        entries = [e for e in kept if e.get("family") == family]
    if not entries:
        raise ValueError(f"Group B keeps no {family!r} question in the selection")
    return entries


def skeleton_from_selection(selection: dict, *, group: str, family: str, part=None,
                            corpus_path=None, name: str | None = None) -> et.TruthFile:
    """A truth skeleton for the selection's kept questions of one family and group.

    The selection file's order is kept, and `part` splits it in halves in that order
    (the first half has the extra question), so two agents can share a family.
    """
    g = _group(group)
    entries = _halves(_selected(selection, g, family), part)
    bodies = {v.id: v for v in corpus.load_all_definitions(corpus_path)}
    meta = corpus.variant_meta(corpus_path)
    questions = []
    for entry in entries:
        vid = entry["id"]
        variant = bodies.get(vid)
        if variant is None:
            raise ValueError(f"{vid} is not in the corpus")
        if (meta.get(vid) or {}).get("status") != "active":
            raise ValueError(f"{vid} is retired in the corpus")
        if len(variant.turns) != 1:
            raise ValueError(f"{vid} has {len(variant.turns)} turns; this POC is single-turn only")
        turn = variant.turns[0]
        flags: list[str] = []
        if g == "B" and (entry.get("rest_routed_today")
                         or _REST_ROUTED_RULE.match(str(entry.get("reason") or ""))):
            flags.append(et.FLAG_REST_ROUTED_TODAY)
            for c in turn.pass_criteria:
                # Endpoint names only: some criteria assert the UID inside the path.
                if (c.field == "api_plan.endpoint" and isinstance(c.value, str)
                        and _ENDPOINT_NAME.fullmatch(c.value)):
                    flag = et.REST_ENDPOINT_FLAG_PREFIX + c.value
                    if flag not in flags:
                        flags.append(flag)
        questions.append(et.TruthQuestion(
            id=vid, family=variant.family or family, group=g, source="corpus",
            turns=[et.TruthTurn(label=turn.label, query=turn.query, reading="", oracle=None,
                                expected=et.Expected(kind="count"))],
            flags=flags, corpus_numbers=_corpus_numbers(turn)))
    default = f"{g.lower()}_{family}" + (f"_{part}" if part else "")
    return et.TruthFile(name=name or default, group=g, questions=questions)


def skeleton_from_questions(questions, *, source: str, group: str, name: str) -> et.TruthFile:
    """A truth skeleton for the ladder or B2 question file: a list (or `{"questions": [...]}`)
    of `{id, query}`, each with an optional `family` (default: the source), `label` and `flags`."""
    if source not in ("ladder", "b2"):
        raise ValueError(f"--source is ladder or b2, not {source!r}")
    g = _group(group)
    items = questions.get("questions") if isinstance(questions, dict) else questions
    if not isinstance(items, list) or not items:
        raise ValueError("the questions file holds no questions")
    seen: set[str] = set()
    out = []
    for item in items:
        qid, query = item.get("id"), item.get("query")
        if not qid or not query:
            raise ValueError(f"every question needs an id and a query: {item!r}")
        if qid in seen:
            raise ValueError(f"{qid} appears twice in the questions file")
        seen.add(qid)
        out.append(et.TruthQuestion(
            id=qid, family=item.get("family") or source, group=g, source=source,
            turns=[et.TruthTurn(label=item.get("label") or "main", query=query, reading="",
                                oracle=None, expected=et.Expected(kind="count"))],
            flags=list(item.get("flags") or [])))
    return et.TruthFile(name=name, group=g, questions=out)


# ── the cases files ──────────────────────────────────────────────────────────

def load_truth_dir(directory, group: str) -> list[et.TruthQuestion]:
    """Every question of one group from the truth files in `directory`, in file-name order."""
    g = group if group in ("A", "B") else _group(group)
    out: list[et.TruthQuestion] = []
    for path in et.truth_paths(directory):
        truth = et.load_truth(path)
        if truth.group == g:
            out += [q for q in truth.questions if q.group == g]
    return out


def criteria_for(expected: et.Expected) -> list[dict]:
    """The engine-neutral criteria for one turn: the reply states each required number
    (and item) of the primary reading, the entity step resolves each expected type, and
    the turn produced an outcome. Alternates are the scorer's, not the harness's."""
    numbers, items = et.primary_requirements(expected)
    crits = [{"field": "last_reply", "op": "matches_re", "value": et.number_patterns(n).pattern}
             for n in numbers]
    crits += [{"field": "last_reply", "op": "matches_re", "value": et.item_pattern(str(i)).pattern}
              for i in items]
    crits += [{"field": "entity_sampletype_codes", "op": "contains", "value": code}
              for code in expected.sampletypes]
    crits.append({"field": "outcome_observed", "op": "true", "value": None})
    assert {(c["field"], c["op"]) for c in crits} <= ENGINE_NEUTRAL
    return crits


def _variant(question: et.TruthQuestion) -> dict:
    return {
        "family": question.family, "id": question.id, "name": question.id,
        "tags": [ENGINE_COMPARE_TAG, f"group:{question.group}"], "requires_env": [],
        "turns": [{"label": t.label, "query": t.query, "pass_criteria": criteria_for(t.expected)}
                  for t in question.turns],
    }


def _is_broad(question) -> bool:
    return et.FLAG_BROAD_MATCH in question.flags


def _ordered(questions: list) -> list:
    """Corpus families first (in first-appearance order), then the ladder, then B2; the
    broad_match questions after everything else, in the same order."""
    first_seen: dict[tuple, int] = {}
    for q in questions:
        first_seen.setdefault((q.source, q.family), len(first_seen))

    def key(q):
        return (_is_broad(q), _SOURCE_RANK.get(q.source, 9), first_seen[(q.source, q.family)])

    return sorted(questions, key=key)   # stable: file order within a block


def catalog_payload(questions: list, group: str, title: str) -> dict:
    """A catalog-shaped cases file: one block per original family (broad questions in
    trailing blocks of their own, so they run last)."""
    families: dict[str, dict] = {}
    for q in _ordered(questions):
        block = f"{q.family}__broad_match" if _is_broad(q) else q.family
        families.setdefault(block, {
            "description": f"{title}: Group {group} {q.family} questions"
                           + (" matching more than 50,000 samples" if _is_broad(q) else ""),
            "variants": []})["variants"].append(_variant(q))
    return {"_note": f"graph_search Nessie POC, {title}; built by build_engine_cases.py from "
                     f"the truth files. Engine-neutral criteria only; the scorer owns the rest.",
            "families": families}


def _is_rest_routed(q) -> bool:
    return et.FLAG_REST_ROUTED_TODAY in q.flags


def _endpoint(q) -> str | None:
    for flag in q.flags:
        if flag.startswith(et.REST_ENDPOINT_FLAG_PREFIX):
            return flag[len(et.REST_ENDPOINT_FLAG_PREFIX):]
    return "parents_by_child_types" if q.id.startswith("pbct.") else "sample-tree"


PILOT_QUOTAS = {
    "A": (
        ("corpus sample_search", 5, lambda q: q.source == "corpus" and q.family == "sample_search"),
        ("corpus harmonization", 1, lambda q: q.source == "corpus" and q.family == "harmonization"),
        ("corpus vocabulary_resolution", 1,
         lambda q: q.source == "corpus" and q.family == "vocabulary_resolution"),
        ("ladder", 2, lambda q: q.source == "ladder"),
        ("B2", 1, lambda q: q.source == "b2"),
    ),
    "B": (
        ("graph_traversal", 3, lambda q: q.family == "graph_traversal"),
        ("graph-native lineage_tree", 1,
         lambda q: q.family == "lineage_tree" and not _is_rest_routed(q)),
        ("REST-routed lineage_tree, a tree", 1,
         lambda q: q.family == "lineage_tree" and _is_rest_routed(q) and _endpoint(q) == "sample-tree"),
        ("REST-routed lineage_tree, parents by child types", 1,
         lambda q: q.family == "lineage_tree" and _is_rest_routed(q)
         and _endpoint(q) == "parents_by_child_types"),
        ("study-scoped sample_search", 1, lambda q: q.family == "sample_search"),
        ("investigation inventory", 1, lambda q: q.family == "project_summary_report"),
    ),
}


def _spread(pool: list, k: int) -> list:
    """k picks spaced evenly through `pool`, deterministic for a fixed input."""
    return [pool[(2 * i + 1) * len(pool) // (2 * k)] for i in range(k)]


def pick_pilot(questions: list, group: str, n: int) -> list[str]:
    """The pilot's ids, per the plan's quotas, spaced evenly through each quota's
    candidates in truth order. A broad_match question is taken only when a quota cannot
    be met without one. `n` is 0 (no pilot) or the quotas' sum."""
    quotas = PILOT_QUOTAS[group]
    total = sum(k for _, k, _ in quotas)
    if n == 0:
        return []
    if n != total:
        raise ValueError(f"the Group {group} pilot is {total} questions by quota "
                         f"({', '.join(f'{k} {name}' for name, k, _ in quotas)}); got --pilot {n}")
    taken: list[str] = []
    for name, k, wanted in quotas:
        candidates = [q for q in questions if wanted(q) and q.id not in taken]
        narrow = [q for q in candidates if not _is_broad(q)]
        pool = narrow if len(narrow) >= k else narrow + [q for q in candidates if _is_broad(q)]
        if len(pool) < k:
            raise ValueError(f"the pilot needs {k} {name} question(s); the truth has {len(pool)}")
        taken += [q.id for q in _spread(pool, k)]
    return taken


def build_cases(questions: list, group: str, pilot: int) -> dict[str, dict]:
    """{file name: payload} for group, pilot and rest."""
    ids = [q.id for q in questions]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"question ids appear in more than one truth question: {dupes}")
    known = set(ids)
    dangling = sorted({q.merged_into for q in questions if q.merged_into and q.merged_into not in known})
    if dangling:
        raise ValueError(f"merged_into names a question no truth file holds: {dangling}")
    runnable = [q for q in questions if q.scorable and not q.merged_into]
    if not runnable:
        raise ValueError(f"no scorable Group {group} question in the truth files")
    for q in runnable:
        if len(q.turns) != 1:
            raise ValueError(f"{q.id} has {len(q.turns)} turns; this POC is single-turn only")
    pilot_ids = pick_pilot(runnable, group, pilot)
    g = group.lower()
    files = {f"group-{g}.json": catalog_payload(runnable, group, f"Group {group}, every question")}
    if pilot_ids:
        chosen = set(pilot_ids)
        files[f"pilot-{g}.json"] = catalog_payload([q for q in runnable if q.id in chosen],
                                                   group, f"Group {group} pilot")
        files[f"rest-{g}.json"] = catalog_payload([q for q in runnable if q.id not in chosen],
                                                  group, f"Group {group}, after the pilot")
    return files


# ── the command line ─────────────────────────────────────────────────────────

def _private_dir(path: Path) -> None:
    if not path.is_dir():
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)


def _write_private(path: Path, text: str) -> None:
    _private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_engine_cases.py",
        description="Truth skeletons and --cases files for the graph_search Nessie POC.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--skeleton", action="store_true", help="a truth skeleton from the selection")
    mode.add_argument("--questions", metavar="FILE", help="a truth skeleton from a question file")
    mode.add_argument("--cases", action="store_true", help="the cases files from the truth")
    parser.add_argument("--selection", help="selection.json (with --skeleton)")
    parser.add_argument("--group", choices=("a", "b", "A", "B"))
    parser.add_argument("--family", help="a family, or 'other' for Group B's remaining families")
    parser.add_argument("--part", type=int, choices=(1, 2), help="the first or second half")
    parser.add_argument("--source", choices=("ladder", "b2"), help="with --questions")
    parser.add_argument("--out", help="the truth file to write (--skeleton, --questions)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --out")
    parser.add_argument("--corpus", default=None, help="corpus.json (default: the harness's own)")
    parser.add_argument("--truth", help="the truth directory (with --cases)")
    parser.add_argument("--out-dir", help="where the cases files go (with --cases)")
    parser.add_argument("--pilot", type=int, help="the pilot size: 10 for a, 8 for b, or 0")
    args = parser.parse_args(argv)

    def need(*names):
        missing = [f"--{n.replace('_', '-')}" for n in names if getattr(args, n) in (None, "")]
        if missing:
            parser.error(f"this mode needs {', '.join(missing)}")

    try:
        if args.cases:
            need("group", "truth", "out_dir", "pilot")
            group = _group(args.group)
            files = build_cases(load_truth_dir(args.truth, group), group, args.pilot)
            out_dir = Path(args.out_dir)
            for name, payload in files.items():
                _write_private(out_dir / name, json.dumps(payload, indent=2) + "\n")
                count = sum(len(b["variants"]) for b in payload["families"].values())
                print(f"wrote {out_dir / name}: {count} question(s)")
            return 0
        need("group", "out")
        out = Path(args.out)
        if out.exists() and not args.force:
            parser.error(f"{out} exists; a truth file may hold derived answers. Give --force "
                         f"to replace it, or another --out.")
        if args.skeleton:
            need("selection", "family")
            selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
            truth = skeleton_from_selection(selection, group=args.group, family=args.family,
                                            part=args.part, corpus_path=args.corpus, name=out.stem)
        else:
            need("source")
            questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
            truth = skeleton_from_questions(questions, source=args.source, group=args.group,
                                            name=out.stem)
    except ValueError as exc:
        parser.error(str(exc))
    _write_private(out, truth.model_dump_json(indent=2) + "\n")
    print(f"wrote {out}: {len(truth.questions)} question(s), group {truth.group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
