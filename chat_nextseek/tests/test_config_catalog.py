import json
from pathlib import Path


def test_every_profile_has_wizard_builder_agent():
    catalog_path = Path(__file__).resolve().parents[1] / "agent_model_catalog.json"
    catalog = json.loads(catalog_path.read_text())
    for profile_key, profile in catalog.items():
        if not isinstance(profile, dict):
            continue
        # The grouped format has "models"; legacy has agent keys directly.
        if "models" in profile:
            agents_in_profile = {
                agent
                for spec in profile["models"].values()
                for entry in (spec if isinstance(spec, list) else [spec])
                for agent in entry["agents"]
            }
        else:
            agents_in_profile = set(profile.keys())
        assert "wizard_builder" in agents_in_profile, (
            f"profile '{profile_key}' missing wizard_builder agent"
        )
