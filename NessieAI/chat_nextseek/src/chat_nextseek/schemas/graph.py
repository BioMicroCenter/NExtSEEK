from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema


class GraphAgentPlan(BaseModel):
    cypher: str = Field(..., description="The Cypher query to execute against Neo4j")
    explanation: str = Field("", description="One-sentence explanation of what the query does")
    parameters: dict = Field(default_factory=dict, description="Named parameters referenced via $param in the query")
    context_mode: str | None = Field(
        None,
        description=(
            "Set by the graph agent's code, never by the model: 'catalog' when the live v1.1 catalog was the "
            "schema context, 'fallback' when the committed JSON schema was (spec D15)."
        ),
    )
    # Left out of the JSON schema, so the model's tool schema never names it: only the graph agent's code sets it.
    context_fallback: SkipJsonSchema[dict | None] = Field(
        None,
        description=(
            "On a 'fallback' plan, why the live catalog was not used (unavailable_reason) and when the committed "
            "schema was captured (fallback_fetched_at, None when the file does not say); None on a 'catalog' plan."
        ),
    )
