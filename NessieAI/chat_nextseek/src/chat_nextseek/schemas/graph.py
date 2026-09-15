from pydantic import BaseModel, Field


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
