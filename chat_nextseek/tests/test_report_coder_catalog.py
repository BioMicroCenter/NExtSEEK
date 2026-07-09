import json
from pathlib import Path


def _agents_in_profile(body: dict) -> set:
    agents = set()
    for spec in (body.get("models") or {}).values():
        for s in (spec if isinstance(spec, list) else [spec]):
            agents.update(s.get("agents") or [])
    return agents


def test_report_coder_registered_in_every_profile():
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "agent_model_catalog.json").read_text()
    )
    for profile, body in catalog.items():
        if profile.startswith("_"):
            continue
        assert "report_coder" in _agents_in_profile(body), (
            f"report_coder missing from profile {profile!r}"
        )
