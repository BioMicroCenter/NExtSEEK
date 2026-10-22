#!/usr/bin/env python3
"""Re-pin a probe's numeric criteria for the instance it is about to run against.

Every number in an acceptance probe is a measurement of one graph. Run the same probe
against another box and each pinned number becomes a false red, which buries the real
findings under arithmetic. So a probe that travels carries a ``_measure`` block: for each
case, the Cypher that measures its truth and the value that Cypher returned where the
probe was written. This script does the two halves of moving it:

    # 1. on the target box, print the read-only batch and run it
    python pin_probe_truths.py --emit-cypher probes/<probe>.json

    # 2. turn cypher-shell's plain output into the answers file ({case_id: number} or
    #    {case_id: [n, m]}): the batch labels each case, so this is mechanical
    python pin_probe_truths.py --parse-output pin.out --probe probes/<probe>.json --measured-out measured.json

    # 3. rewrite the criteria
    python pin_probe_truths.py --pin probes/<probe>.json --from measured.json [--out new.json]

Nothing here talks to a database: the transport differs per box (docker exec locally,
ssh plus sudo on dev, a direct key on prod) and a tool that guesses it would be wrong
somewhere. It prints Cypher and it rewrites JSON.

A criterion is found by the guarded number pattern the probe already carries, so a
mis-stated ``_measure`` block fails loudly here rather than silently at run time.

``mode`` says how a case's several numbers relate. ``any`` (the default) is one criterion
that accepts an alternation: two readings of the question are both right, as with the two
SRP scopes. ``each`` is one criterion per number, all of which must appear: a spellings
question is answered by naming every spelling, and an alternation there would pass on one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: The pattern a number criterion uses: no match inside a longer number, a decimal, a
#: date or a UID suffix. Kept identical to what the probes are written with.
_GUARD_PREFIX = r"(?<![\w.,/-])"
_GUARD_SUFFIX = r"(?![\w]|[.,]\d|[-/]\d)"


def number_pattern(values: list[int]) -> str:
    """The guarded regex for one measured number, or an alternation for several."""
    forms = [f"{v:,}".replace(",", ",?") for v in values]
    body = forms[0] if len(forms) == 1 else "(" + "|".join(forms) + ")"
    return _GUARD_PREFIX + body + _GUARD_SUFFIX


def _as_list(value) -> list[int]:
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(value)]


def variants(spec: dict):
    for family in (spec.get("families") or {}).values():
        for variant in family.get("variants") or []:
            yield variant


def emit_cypher(spec: dict) -> str:
    """The read-only batch, in the probe's own order, one labelled statement per case.

    Comments are Cypher's `//`: cypher-shell rejects a `--` line outright, which is how the
    first run on the dev box (2026-09-22) produced four lines of error and no numbers. Each
    case also opens with `RETURN '<case_id>' AS case`, so the output labels its own numbers
    instead of relying on statement order.
    """
    measure = spec.get("_measure") or {}
    if not measure:
        return "// this probe carries no _measure block, so no number travels with it"
    lines = ["// Read-only. Pipe into cypher-shell (--format plain), then put the answers in a JSON",
             "// file as {case_id: number} (or {case_id: [n, m]} where two are acceptable)",
             "// and re-pin with:  pin_probe_truths.py --pin <probe> --from <that file>", ""]
    for case_id, entry in measure.items():
        lines.append(f"// {case_id}: here {entry.get('locals')}")
        lines.append(f"RETURN '{case_id}' AS case;")
        for statement in _as_list_str(entry.get("cypher")):
            lines.append(statement.strip().rstrip(";") + ";")
        lines.append("")
    return "\n".join(lines)


def _as_list_str(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value or "")]


def pin(spec: dict, measured: dict) -> tuple[dict, list[str]]:
    """Rewrite each measured case's number criterion. Returns the spec and a change log."""
    measure = spec.get("_measure") or {}
    by_id = {v["id"]: v for v in variants(spec)}
    log: list[str] = []
    for case_id, value in measured.items():
        if case_id not in measure:
            raise SystemExit(f"{case_id} is not in the probe's _measure block")
        if case_id not in by_id:
            raise SystemExit(f"{case_id} is not a case in this probe")
        entry = measure[case_id]
        olds, news = _as_list(entry["locals"]), _as_list(value)
        if entry.get("mode") == "each":
            if len(olds) != len(news):
                raise SystemExit(f"{case_id}: mode 'each' needs {len(olds)} numbers, got {len(news)}")
            pairs = [([o], [n]) for o, n in zip(olds, news)]
        else:
            pairs = [(olds, news)]
        for old_values, new_values in pairs:
            old_pattern = number_pattern(old_values)
            new_pattern = number_pattern(new_values)
            hits = [c for turn in by_id[case_id]["turns"] for c in turn["pass_criteria"]
                    if c.get("op") == "matches_re" and c.get("value") == old_pattern]
            if not hits:
                raise SystemExit(
                    f"{case_id}: no criterion carries the pattern for {old_values!r}. The _measure "
                    "block and the criteria have drifted apart; fix the probe. (A case that states "
                    "several numbers as separate criteria needs \"mode\": \"each\".)")
            for criterion in hits:
                criterion["value"] = new_pattern
            log.append(f"{case_id}: {old_pattern} -> {new_pattern}")
        entry["locals"] = news
    return spec, log


def parse_output(text: str) -> dict[str, list[int]]:
    """The numbers each case's statements returned, in order, from `cypher-shell --format plain`.

    The batch `emit_cypher` prints opens every case with `RETURN '<case_id>' AS case`, so the
    output carries a `case` header and the quoted id before that case's result rows. A row's
    number is its last comma-separated cell; header rows (`count(n)`, `species, n`) have none.
    """
    out: dict[str, list[int]] = {}
    lines = [ln.rstrip() for ln in text.splitlines()]
    current = None
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if ln in ("case", "sep") and nxt.startswith('"') and nxt.endswith('"'):
            current = nxt.strip('"') if ln == "case" else current
            if ln == "case":
                out.setdefault(current, [])
            i += 2
            continue
        if current is not None and ln:
            last = ln.rsplit(",", 1)[-1].strip().strip('"')
            if re.fullmatch(r"-?\d+", last):
                out[current].append(int(last))
        i += 1
    return out


def measured_from_output(spec: dict, parsed: dict[str, list[int]]) -> tuple[dict, list[str], list[str]]:
    """({case_id: number or [numbers]}, cases to drop, cases to check by hand)."""
    measure = spec.get("_measure") or {}
    measured, drop, by_hand = {}, [], []
    for case_id, entry in measure.items():
        want = len(_as_list(entry.get("locals")))
        got = parsed.get(case_id)
        if got is None:
            by_hand.append(f"{case_id}: not in the output (did the batch run to the end?)")
        elif len(got) != want:
            by_hand.append(f"{case_id}: {len(got)} number(s) {got} where the _measure block holds {want}")
        elif 0 in got:
            drop.append(f"{case_id}: measured {got}. A 0 from a scope the box does not hold is not a "
                        "truth: drop this case from the run")
        else:
            measured[case_id] = got[0] if want == 1 else got
    return measured, drop, by_hand


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit-cypher", type=Path, metavar="PROBE")
    ap.add_argument("--pin", type=Path, metavar="PROBE")
    ap.add_argument("--from", dest="measured", type=Path, metavar="JSON")
    ap.add_argument("--out", type=Path, help="default: rewrite the probe in place")
    ap.add_argument("--parse-output", type=Path, metavar="OUT", help="cypher-shell's output of the batch")
    ap.add_argument("--probe", type=Path, help="with --parse-output: the probe the batch came from")
    ap.add_argument("--measured-out", type=Path, help="with --parse-output: where to write the answers")
    args = ap.parse_args(argv)

    if args.parse_output:
        if not (args.probe and args.measured_out):
            ap.error("--parse-output needs --probe and --measured-out")
        spec = json.loads(args.probe.read_text(encoding="utf-8"))
        parsed = parse_output(args.parse_output.read_text(encoding="utf-8", errors="replace"))
        measured, drop, by_hand = measured_from_output(spec, parsed)
        args.measured_out.write_text(json.dumps(measured, indent=1) + "\n", encoding="utf-8")
        for d in drop:
            print(f"DROP: {d}")
        for b in by_hand:
            print(f"BY HAND: {b}")
        print(f"wrote {args.measured_out}: {len(measured)} case(s) measured, {len(drop)} to drop, "
              f"{len(by_hand)} to check by hand")
        return 2 if by_hand else 0

    if args.emit_cypher:
        print(emit_cypher(json.loads(args.emit_cypher.read_text(encoding="utf-8"))))
        return 0
    if not (args.pin and args.measured):
        ap.error("either --emit-cypher PROBE, or --pin PROBE --from JSON")
    spec = json.loads(args.pin.read_text(encoding="utf-8"))
    measured = json.loads(args.measured.read_text(encoding="utf-8"))
    spec, log = pin(spec, measured)
    out = args.out or args.pin
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join(log) or "nothing to re-pin")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
