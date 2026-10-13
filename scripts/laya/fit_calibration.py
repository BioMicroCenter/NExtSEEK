"""Fit the laya temperature and gate threshold, write NessieAI/router/laya_calibration.json (SPEC s8, s9).

Input: a jsonl of scored calibration-slice rows, one per question:
  {"probabilities": {route: p, ...}, "teacher_route": str, "truth_route": str|null, "either": bool}
`probabilities` are the sidecar's unrounded output from the same laya-serve image, checkpoint and precision as the
box, not the training script's own logits.

Temperature: one value for (choice, 3 options), by NLL against the teacher. Threshold: the lowest t in
0.50..0.99 (step 0.01) whose agreement is >= 98% (one point of margin over the 97% bar).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from NessieAI.router import laya_common  # noqa: E402

EPS = 1e-12
AGREEMENT_TARGET = 0.98
THRESHOLDS = [round(0.50 + 0.01 * i, 2) for i in range(50)]


def _labelled(rows):
    return [r for r in rows if r.get("teacher_route") in r.get("probabilities", {})]


def nll(rows, T: float) -> float:
    rows = _labelled(rows)
    return -sum(math.log(max(laya_common.apply_temperature(r["probabilities"], T)[r["teacher_route"]], EPS))
                for r in rows) / len(rows)


def fit_temperature(rows, lo: float = 0.05, hi: float = 20.0) -> float:
    """NLL is convex in beta = 1/T, so a ternary search on beta is exact enough."""
    a, b = 1.0 / hi, 1.0 / lo
    for _ in range(100):
        m1, m2 = a + (b - a) / 3, b - (b - a) / 3
        if nll(rows, 1.0 / m1) < nll(rows, 1.0 / m2):
            b = m2
        else:
            a = m1
    return round(1.0 / ((a + b) / 2), 4)


def _top(probs):
    return max(probs, key=probs.get)


def _correct(row, route) -> bool | None:
    if row.get("either"):
        return route in ("nextseek_query", "container_cc")
    return None if not row.get("truth_route") else route == row["truth_route"]


def coverage_curve(rows, T: float):
    """For each t: coverage (gate-passing share), agreement with the teacher, accuracy vs truth where known."""
    rows = _labelled(rows)
    scored = []
    for r in rows:
        p = laya_common.apply_temperature(r["probabilities"], T)
        top = _top(p)
        scored.append((p[top], top == r["teacher_route"], _correct(r, top)))
    curve = []
    for t in THRESHOLDS:
        passed = [s for s in scored if s[0] >= t]
        acc = [s[2] for s in passed if s[2] is not None]
        curve.append({"t": t, "n": len(passed), "coverage": len(passed) / len(scored),
                      "agreement": sum(s[1] for s in passed) / len(passed) if passed else None,
                      "accuracy": sum(acc) / len(acc) if acc else None})
    return curve


def pick_threshold(curve):
    """Lowest t whose agreement point estimate is >= 98%, or None (then no shadow go is asked)."""
    return next((c["t"] for c in curve if c["n"] and c["agreement"] >= AGREEMENT_TARGET), None)


def revision_for(weights: pathlib.Path, date: str | None = None) -> str:
    """`<yyyymmdd>-<first 12 hex of the weights file's sha256>` (SPEC s6.2)."""
    date = date or datetime.date.today().strftime("%Y%m%d")
    return f"{date}-{hashlib.sha256(pathlib.Path(weights).read_bytes()).hexdigest()[:12]}"


def calibration_doc(rows, revision: str, options: dict, prompt_hash: str, date_iso: str | None = None):
    T = fit_temperature(rows)
    curve = coverage_curve(rows, T)
    t = pick_threshold(curve)
    if t is None:
        raise SystemExit("no threshold reaches 98% agreement on the calibration slice; report back, no shadow go")
    return {"revision": revision, "options_hash": laya_common.options_hash(options), "prompt_hash": prompt_hash,
            "question_type": "choice", "option_count": len(options["options"]), "temperature": T, "threshold": t,
            "fitted_on": {"n": len(_labelled(rows)), "date": date_iso or datetime.date.today().isoformat()}}, curve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scores", required=True, help="scored calibration-slice jsonl")
    ap.add_argument("--weights", required=True, help="the weights file the revision names")
    ap.add_argument("--options", default=str(REPO / "NessieAI/router/laya_options.json"))
    ap.add_argument("--out", default=str(REPO / "NessieAI/router/laya_calibration.json"))
    ap.add_argument("--curve", help="also write the coverage curve json here (outside the repo)")
    a = ap.parse_args(argv)
    rows = [json.loads(ln) for ln in pathlib.Path(a.scores).read_text().splitlines() if ln.strip()]
    options = json.loads(pathlib.Path(a.options).read_text(encoding="utf-8"))
    doc, curve = calibration_doc(rows, revision_for(pathlib.Path(a.weights)), options, laya_common.prompt_hash())
    pathlib.Path(a.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    if a.curve:
        pathlib.Path(a.curve).write_text(json.dumps(curve, indent=1), encoding="utf-8")
    print(f"T={doc['temperature']} threshold={doc['threshold']} n={doc['fitted_on']['n']} revision={doc['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
