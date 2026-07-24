from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_every_route_gate_case_asserts_route():
    ov = corpus.load_overlay(OVERLAY)
    gate = [v for v in ov if "route_gate" in v.tags]
    assert len(gate) >= 3
    for v in gate:
        fields = {c.field for t in v.turns for c in t.pass_criteria}
        assert "route" in fields, f"{v.id} missing a route assertion"


def test_has_cc_unrelated_and_green_families():
    ov = corpus.load_overlay(OVERLAY)
    fams = {v.family for v in ov}
    assert {"nessie_route", "nessie_green"} <= fams
    routes = {c.value for v in ov for t in v.turns for c in t.pass_criteria if c.field == "route"}
    assert {"container_cc", "unrelated", "nextseek_query"} <= routes
