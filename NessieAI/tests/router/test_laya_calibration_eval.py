"""JevLevROUTING U3: fit_calibration.py and evaluate.py on tiny hand-checkable made-up files (SPEC test 12)."""
import importlib.util
import json
import math
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location("laya_" + name, REPO / f"scripts/laya/{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fc = _load("fit_calibration")
NS, CC, UN = "nextseek_query", "container_cc", "unrelated"


def row(p_ns, p_cc, teacher, truth=None, either=False):
    return {"probabilities": {NS: p_ns, CC: p_cc, UN: round(1 - p_ns - p_cc, 12)}, "teacher_route": teacher,
            "truth_route": truth, "either": either}


def test_fit_temperature_matches_a_grid_search_and_cools_overconfidence():
    # always 0.9 NS but right only 3 times in 4: the best temperature softens to p(NS)=0.75
    rows = [row(0.9, 0.05, NS)] * 3 + [row(0.9, 0.05, CC)]
    T = fc.fit_temperature(rows)
    assert T > 1.0
    best = min((fc.nll(rows, g / 100), g / 100) for g in range(30, 600))[1]
    assert abs(T - best) < 0.02
    assert math.isclose(fc.laya_common.apply_temperature(rows[0]["probabilities"], T)[NS], 0.75, abs_tol=0.01)


def test_fit_temperature_sharpens_an_underconfident_model():
    rows = [row(0.5, 0.3, NS)] * 4
    assert fc.fit_temperature(rows) < 1.0


def test_coverage_curve_hand_numbers():
    # T=1. confidences .9 (right), .8 (right), .6 (wrong), .55 (right)
    rows = [row(0.9, 0.05, NS), row(0.1, 0.8, CC), row(0.6, 0.2, CC), row(0.55, 0.3, NS)]
    c = {x["t"]: x for x in fc.coverage_curve(rows, 1.0)}
    assert (c[0.5]["n"], c[0.5]["agreement"]) == (4, 0.75)
    assert (c[0.7]["n"], c[0.7]["coverage"], c[0.7]["agreement"]) == (2, 0.5, 1.0)
    assert c[0.99]["n"] == 0 and c[0.99]["agreement"] is None
    assert c[0.56]["agreement"] == pytest.approx(2 / 3)
    assert fc.pick_threshold(list(c.values())) == 0.61


def test_pick_threshold_is_the_lowest_t_at_98_percent_and_none_when_never():
    curve = [{"t": 0.5, "n": 10, "agreement": 0.9}, {"t": 0.6, "n": 9, "agreement": 0.98},
             {"t": 0.7, "n": 5, "agreement": 1.0}]
    assert fc.pick_threshold(curve) == 0.6
    assert fc.pick_threshold([{"t": 0.5, "n": 4, "agreement": 0.97}]) is None
    assert fc.pick_threshold([{"t": 0.5, "n": 0, "agreement": None}]) is None


def test_accuracy_counts_either_as_right_for_both_routes():
    rows = [row(0.9, 0.05, NS, truth=CC), row(0.9, 0.05, NS, either=True)]
    (c,) = [x for x in fc.coverage_curve(rows, 1.0) if x["t"] == 0.5]
    assert c["accuracy"] == 0.5


def test_revision_and_doc_and_cli(tmp_path):
    w = tmp_path / "w.bin"
    w.write_bytes(b"weights")
    import hashlib
    assert fc.revision_for(w, "20261003") == "20261003-" + hashlib.sha256(b"weights").hexdigest()[:12]
    rows = [row(0.97, 0.01, NS)] * 20 + [row(0.2, 0.5, CC)] * 5
    options = {"options": [{"key": NS, "text": "a"}, {"key": CC, "text": "b"}, {"key": UN, "text": "c"}]}
    doc, curve = fc.calibration_doc(rows, "r", options, "p" * 64, "2026-10-03")
    assert set(doc) == {"revision", "options_hash", "prompt_hash", "question_type", "option_count", "temperature",
                        "threshold", "fitted_on"}
    assert doc["question_type"] == "choice" and doc["option_count"] == 3 and 0.5 <= doc["threshold"] <= 0.99
    assert doc["fitted_on"] == {"n": 25, "date": "2026-10-03"}
    s = tmp_path / "s.jsonl"
    s.write_text("".join(json.dumps(r) + "\n" for r in rows))
    o = tmp_path / "o.json"
    o.write_text(json.dumps(options))
    out = tmp_path / "cal.json"
    assert fc.main(["--scores", str(s), "--weights", str(w), "--options", str(o), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["revision"].endswith(hashlib.sha256(b"weights").hexdigest()[:12])


def test_calibration_fails_when_no_threshold_reaches_the_target():
    rows = [row(0.9, 0.05, CC)] * 4
    with pytest.raises(SystemExit, match="no shadow go"):
        fc.calibration_doc(rows, "r", {"options": []}, "p")
