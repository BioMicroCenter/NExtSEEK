import json
from pathlib import Path


def _agent_route(profile, agent):
    """In a grouped-format profile, return (provider, model) an agent is routed to."""
    for model_name, spec in profile.get("models", {}).items():
        for entry in (spec if isinstance(spec, list) else [spec]):
            if agent in entry.get("agents", []):
                return entry.get("provider"), model_name
    return None, None


def test_pipeline_agent_routed_to_tool_capable_provider_in_default():
    """The full-agentic pipeline_agent runs a chat_with_tools function-calling
    loop, which only the Bedrock client (provider 'anth') implements. In the
    shipping 'default' (mixed) profile it must NOT be routed to gcp/GeminiClient,
    which has no chat_with_tools and would break the agent loop."""
    catalog_path = Path(__file__).resolve().parents[1] / "agent_model_catalog.json"
    catalog = json.loads(catalog_path.read_text())
    provider, model = _agent_route(catalog["default"], "pipeline_agent")
    assert provider == "anth", (
        f"pipeline_agent routed to provider={provider!r} (model={model!r}); "
        "must be 'anth' (Bedrock) — only BedrockClient implements chat_with_tools."
    )
