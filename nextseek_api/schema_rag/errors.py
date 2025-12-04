"""
Error codes and messages for Schema RAG.

This module provides:
- Symbolic error codes for ingestion and retrieval failures
- Natural-language error message templates
- Helper function for formatting error messages

Implementation: Phase 5
"""

# -----------------------------
# Symbolic Error Codes
# -----------------------------

SCHEMA_FETCH_FAILED = "SCHEMA_FETCH_FAILED"
SESSION_MISSING_OR_EXPIRED = "SESSION_MISSING_OR_EXPIRED"
NO_GOOD_MATCH = "NO_GOOD_MATCH"
SCHEMA_TOO_LARGE = "SCHEMA_TOO_LARGE"
EMBEDDING_FAILED = "EMBEDDING_FAILED"

# -----------------------------
# Natural-Language Message Templates
# -----------------------------

ERROR_MESSAGES = {
    SCHEMA_FETCH_FAILED: "UNABLE to RETRIEVE API SCHEMA FOR {schema_url}",
    SESSION_MISSING_OR_EXPIRED: "Schema session is missing or expired. Please provide schema_url to re-ingest.",
    NO_GOOD_MATCH: "No endpoints matched this request with sufficient confidence. Please rephrase the query or use a different tool.",
    SCHEMA_TOO_LARGE: "The provided API schema defines too many endpoints for this tool. Please narrow the schema scope.",
    EMBEDDING_FAILED: "Failed to process query for semantic search. Please try again.",
}

# -----------------------------
# Helper Function
# -----------------------------


def get_error_message(code: str, **kwargs) -> str:
    """Return formatted message for code using kwargs, or a fallback."""
    template = ERROR_MESSAGES.get(code)
    if template is None:
        return f"Unknown error: {code}"
    try:
        return template.format(**kwargs)
    except KeyError:
        # Return template as-is if formatting fails (missing kwargs)
        return template


# -----------------------------
# Exception Classes
# -----------------------------


class SchemaFetchError(Exception):
    """Raised when fetching or parsing a schema fails."""
    pass
