from pydantic import BaseModel, Field


class GraphAgentPlan(BaseModel):
    cypher: str = Field(..., description="The Cypher query to execute against Neo4j")
    explanation: str = Field("", description="One-sentence explanation of what the query does")
    parameters: dict = Field(default_factory=dict, description="Named parameters referenced via $param in the query")
