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


def _agent_route(profile, agent):
    """In a grouped-format profile, return (provider, model) an agent is routed to."""
    for model_name, spec in profile.get("models", {}).items():
        for entry in (spec if isinstance(spec, list) else [spec]):
            if agent in entry.get("agents", []):
                return entry.get("provider"), model_name
    return None, None


def test_pipeline_groupby_routed_to_tool_capable_provider_in_default():
    """pipeline_groupby runs a chat_with_tools function-calling loop, which only
    the Bedrock client (provider 'anth') implements. In the shipping 'default'
    (mixed) profile it must NOT be routed to gcp/GeminiClient, which has no
    chat_with_tools and crashes the group-by step."""
    catalog_path = Path(__file__).resolve().parents[1] / "agent_model_catalog.json"
    catalog = json.loads(catalog_path.read_text())
    provider, model = _agent_route(catalog["default"], "pipeline_groupby")
    assert provider == "anth", (
        f"pipeline_groupby routed to provider={provider!r} (model={model!r}); "
        "must be 'anth' (Bedrock) — only BedrockClient implements chat_with_tools."
    )
