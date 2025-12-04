"""
Schema RAG package for OpenAPI schema ingestion and semantic retrieval.

This package provides functionality for:
- Ingesting OpenAPI schemas into session-scoped DuckDB databases
- Generating embeddings for API endpoints
- Semantic retrieval of relevant endpoints based on natural language queries

Importing this package automatically resolves forward references in the
public API models (nextseek_api.models.RetrieveResponse).
"""

# Import internal models to make them available at package level
from .models import MinimalAPIEndpoint, FullAPIEndpoint, SessionInfo

# Resolve forward references in Phase 1 public API models
# This MUST happen after MinimalAPIEndpoint and FullAPIEndpoint are defined
from nextseek_api.models import _rebuild_schema_rag_models
_rebuild_schema_rag_models()

__all__ = [
    "MinimalAPIEndpoint",
    "FullAPIEndpoint",
    "SessionInfo",
]

