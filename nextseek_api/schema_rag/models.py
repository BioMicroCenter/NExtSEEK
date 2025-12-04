"""
Internal Pydantic models for Schema RAG.

These models represent API endpoints and session metadata used internally
by the schema_rag package for ingestion, storage, and retrieval.
"""

from typing import List, Optional, Literal, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
import json


class MinimalAPIEndpoint(BaseModel):
    """
    Lightweight representation of an API endpoint.
    
    Used for minimal mode retrieval responses where only essential
    endpoint identification information is needed.
    """
    operationId: str = Field(..., description="Unique operation identifier")
    method: Literal["POST", "GET", "PATCH", "DELETE", "PUT"] = Field(
        ..., description="HTTP method"
    )
    tags: Optional[List[str]] = Field(None, description="API tags/categories")
    description: Optional[str] = Field(None, description="Endpoint description")
    path: Optional[str] = Field(None, description="API path template")

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_embedding_text(self) -> str:
        """
        Build text representation for embedding.
        
        Combines operationId, method, path, tags, and description
        into a single text string suitable for semantic embedding.
        """
        parts = []
        
        # Operation identifier
        parts.append(f"operationId: {self.operationId}")
        
        # Method
        parts.append(f"method: {self.method}")
        
        # Path
        if self.path:
            parts.append(f"path: {self.path}")
        
        # Tags
        if self.tags:
            parts.append(f"tags: {', '.join(self.tags)}")
        
        # Description
        if self.description:
            parts.append(f"description: {self.description}")
        
        return "\n".join(parts)


class FullAPIEndpoint(BaseModel):
    """
    Complete representation of an API endpoint.
    
    Includes all endpoint details needed for full mode retrieval,
    including request/response schemas and examples.
    """
    operationId: str = Field(..., description="Unique operation identifier")
    method: Literal["POST", "GET", "PATCH", "DELETE", "PUT"] = Field(
        ..., description="HTTP method"
    )
    tags: Optional[List[str]] = Field(None, description="API tags/categories")
    description: Optional[str] = Field(None, description="Endpoint description")
    path: str = Field(..., description="API path template")
    parameters: Optional[dict] = Field(
        None, description="Path/query/header parameters"
    )
    request_schema: dict = Field(
        ..., description="Simplified, structure-preserving request schema"
    )
    response_schema: Optional[dict] = Field(
        None,
        description="Simplified response schema (stored but NOT used in embeddings or LLM responses)"
    )
    examples: Optional[List[str]] = Field(
        None, description="Dehydrated example strings (NOT raw dicts)"
    )

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_embedding_text(self, include_examples: bool = True) -> str:
        """
        Build the full-text representation for embedding.

        Include: operationId, method, path, tags, description, parameters, request_schema.
        Exclude: response_schema (response bodies are NOT part of the embedding text).

        Uses the simplified request_schema (field names + basic types, nested structure preserved).
        
        Args:
            include_examples: Whether to include example strings in the embedding text.
        
        Returns:
            Full-text representation suitable for semantic embedding.
        """
        parts = []
        
        # Operation identifier
        parts.append(f"operationId: {self.operationId}")
        
        # Method
        parts.append(f"method: {self.method}")
        
        # Path
        parts.append(f"path: {self.path}")
        
        # Tags
        if self.tags:
            parts.append(f"tags: {', '.join(self.tags)}")
        
        # Description
        if self.description:
            parts.append(f"description: {self.description}")
        
        # Parameters
        if self.parameters:
            params_str = json.dumps(self.parameters, separators=(',', ':'))
            parts.append(f"parameters: {params_str}")
        
        # Request schema (simplified, structure-preserving)
        if self.request_schema:
            schema_str = json.dumps(self.request_schema, separators=(',', ':'))
            parts.append(f"request_schema: {schema_str}")
        
        # Examples (dehydrated strings)
        if include_examples and self.examples:
            examples_str = " | ".join(self.examples)
            parts.append(f"examples: {examples_str}")
        
        # Note: response_schema is intentionally excluded from embedding text
        
        return "\n".join(parts)


class SessionInfo(BaseModel):
    """
    Session metadata for a schema ingestion session.
    
    Contains all information needed to manage a session's lifecycle,
    including its DuckDB storage path and expiration details.
    """
    session_id: str = Field(..., description="Unique session identifier")
    db_path: str = Field(..., description="Path to session's DuckDB file")
    created_at: datetime = Field(..., description="Session creation timestamp")
    ttl_minutes: int = Field(..., description="Session TTL in minutes")
    schema_url: str = Field(..., description="URL of the ingested OpenAPI schema")
    num_endpoints: int = Field(..., description="Number of endpoints indexed")

    model_config = ConfigDict(extra='forbid', validate_default=True)

