"""
Embedding, ingestion, and retrieval service for Schema RAG.

This module provides functionality for:
- Generating embeddings for endpoint text representations
- Multi-pass retrieval scoring (minimal -> full -> enriched)
- Query processing and result formatting

Schema fetching, parsing, and simplification are handled by the
OpenAPISchemaProcessor class in schema_processor.py.

Implementation: Phase 6
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings
from sentence_transformers import SentenceTransformer

# Public API models
from nextseek_api.models import (
    IngestError,
    IngestRequest,
    IngestResponse,
    IngestResult,
    RetrieveDebugInfo,
    RetrieveRequest,
    RetrieveResponse,
)

# Internal models
from .models import FullAPIEndpoint, MinimalAPIEndpoint, SessionInfo

# Session lifecycle helpers
from .session import (
    cleanup_expired_sessions,
    create_session,
    is_session_expired,
    load_session_info,
    _now,
)

# Database helpers
from .db import (
    init_session_db,
    insert_endpoints,
    load_all_full_endpoints,
    load_all_minimal_endpoints,
    load_full_endpoints_by_ids,
    load_minimal_endpoints_by_ids,
)

# Error codes and messages
from .errors import (
    EMBEDDING_FAILED,
    NO_GOOD_MATCH,
    SCHEMA_FETCH_FAILED,
    SCHEMA_TOO_LARGE,
    SESSION_MISSING_OR_EXPIRED,
    SchemaFetchError,
    get_error_message,
)

# Schema processor
from .schema_processor import OpenAPISchemaProcessor


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type Aliases
# ---------------------------------------------------------------------------

EndpointScore = Tuple[str, float]  # (endpoint_id, similarity_score)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MIN_SCORE = 0.3
RESOLVED_TERM_BOOST = 0.05


# ---------------------------------------------------------------------------
# Endpoint Filtering
# ---------------------------------------------------------------------------


def _apply_endpoint_filters(
    endpoints: list,
    filter_by: Optional[str],
    filter_terms: Optional[List[str]],
) -> list:
    """
    Filter endpoints by HTTP method or tag before scoring.

    Args:
        endpoints: List of MinimalAPIEndpoint or FullAPIEndpoint objects.
        filter_by: "METHOD" or "TAG", or None to skip filtering.
        filter_terms: Values to match against (already validated/normalized).

    Returns:
        Filtered list of endpoints.
    """
    if filter_by is None or not filter_terms:
        return endpoints

    if filter_by == "METHOD":
        terms_upper = {t.upper() for t in filter_terms}
        return [ep for ep in endpoints if ep.method.upper() in terms_upper]

    if filter_by == "TAG":
        terms_set = set(filter_terms)
        return [
            ep for ep in endpoints
            if ep.tags and any(t in terms_set for t in ep.tags)
        ]

    return endpoints


# ---------------------------------------------------------------------------
# Embedding Utilities
# ---------------------------------------------------------------------------

_EMBEDDER: Optional[SentenceTransformer] = None


def _get_embedder() -> SentenceTransformer:
    """
    Lazily load and cache the sentence-transformers model.
    
    Uses settings.SCHEMA_RAG_EMBEDDING_MODEL_NAME for the model identifier
    and settings.SCHEMA_RAG_EMBEDDING_MODEL_PATH for the local cache directory.
    
    Returns:
        Cached SentenceTransformer model instance.
    """
    global _EMBEDDER
    if _EMBEDDER is None:
        model_name = settings.SCHEMA_RAG_EMBEDDING_MODEL_NAME
        cache_folder = settings.SCHEMA_RAG_EMBEDDING_MODEL_PATH
        logger.info("Loading embedding model: %s", model_name)
        _EMBEDDER = SentenceTransformer(model_name, cache_folder=cache_folder)
    return _EMBEDDER


def embed_texts(texts: List[str]) -> np.ndarray:
    """
    Return L2-normalized embedding matrix of shape (n, d).
    
    Uses cosine similarity by normalizing embeddings to unit vectors.
    
    Args:
        texts: List of text strings to embed.
    
    Returns:
        numpy array of shape (n, d) where n is number of texts and d is embedding dimension.
        Each row is L2-normalized for cosine similarity computation via dot product.
    """
    if not texts:
        return np.array([])
    
    embedder = _get_embedder()
    embeddings = embedder.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    
    # L2 normalize for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Avoid division by zero for empty/null embeddings
    norms = np.where(norms == 0, 1, norms)
    normalized = embeddings / norms
    
    return normalized




# ---------------------------------------------------------------------------
# Endpoint Flattening
# ---------------------------------------------------------------------------

# HTTP methods supported by the FullAPIEndpoint model
SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete"}


def flatten_endpoints(openapi_doc: dict, processor: OpenAPISchemaProcessor) -> List[FullAPIEndpoint]:
    """
    Parse OpenAPI paths/operations into FullAPIEndpoint objects.
    
    - Handles missing operationId by synthesizing "{method} {path}"
    - Extracts and simplifies request/response schemas using the processor
    - Extracts examples from the OpenAPI doc, dehydrates them into strings
    - Skips unusable endpoints
    
    Args:
        openapi_doc: Parsed OpenAPI document dictionary (with $refs already resolved).
        processor: OpenAPISchemaProcessor instance for schema extraction/simplification.
    
    Returns:
        List of FullAPIEndpoint objects.
    """
    endpoints = []
    paths = openapi_doc.get("paths", {})
    
    for path, path_item in paths.items():
        # Skip excluded paths (e.g., meta-endpoints like /schema_rag/)
        excluded_patterns = getattr(settings, 'SCHEMA_RAG_EXCLUDED_PATH_PATTERNS', [])
        if any(pattern in path for pattern in excluded_patterns):
            continue
        
        if not isinstance(path_item, dict):
            continue
        
        for method, operation in path_item.items():
            method_lower = method.lower()
            
            # Skip non-operation keys (parameters, servers, etc.)
            if method_lower not in SUPPORTED_METHODS:
                continue
            
            if not isinstance(operation, dict):
                continue
            
            try:
                # Get or synthesize operationId
                operation_id = operation.get("operationId")
                if not operation_id:
                    operation_id = f"{method.upper()} {path}"
                
                # Extract tags
                tags = operation.get("tags")
                if tags and not isinstance(tags, list):
                    tags = [str(tags)]
                
                # Extract description (prefer summary if description is missing)
                description = operation.get("description") or operation.get("summary")
                
                # Extract and simplify schemas using processor
                request_schema = processor.extract_request_schema(operation)
                response_schema = processor.extract_response_schema(operation)
                parameters = processor.extract_parameters(operation)
                examples = processor.extract_and_dehydrate_examples(operation)
                
                endpoint = FullAPIEndpoint(
                    operationId=operation_id,
                    method=method.upper(),
                    tags=tags,
                    description=description,
                    path=path,
                    parameters=parameters,
                    request_schema=request_schema if request_schema else {},
                    response_schema=response_schema,
                    examples=examples,
                )
                endpoints.append(endpoint)
                
            except Exception as e:
                # Log and skip problematic endpoints
                logger.warning(
                    "Skipping endpoint %s %s due to error: %s",
                    method.upper(), path, e
                )
                continue
    
    return endpoints


# ---------------------------------------------------------------------------
# Ingestion Orchestration
# ---------------------------------------------------------------------------


def ingest_schema(req: IngestRequest) -> IngestResult:
    """
    High-level ingest workflow for OpenAPI schema.
    
    Steps:
    1. cleanup_expired_sessions()
    2. Fetch schema using OpenAPISchemaProcessor (resolves $refs)
    3. flatten_endpoints(openapi_doc, processor)
    4. Enforce SCHEMA_RAG_MAX_ENDPOINTS limit
    5. create_session(...)
    6. init_session_db() + insert_endpoints()
    7. Return IngestResult with IngestResponse or IngestError
    
    Args:
        req: IngestRequest containing schema_url and optional ttl_minutes.
    
    Returns:
        IngestResult with success=True and response, or success=False and error.
    """
    # Step 1: Clean up expired sessions
    try:
        cleanup_expired_sessions()
    except Exception as e:
        logger.warning("Cleanup failed (continuing): %s", e)
    
    # Step 2: Fetch schema using processor (handles $ref resolution)
    processor = OpenAPISchemaProcessor()
    try:
        openapi_doc = processor.fetch_schema(req.schema_url)
    except SchemaFetchError as e:
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_FETCH_FAILED,
                message=get_error_message(SCHEMA_FETCH_FAILED, schema_url=req.schema_url),
                details={"exception": str(e)},
            ),
        )
    except Exception as e:
        logger.exception("Unexpected error fetching schema: %s", e)
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_FETCH_FAILED,
                message=get_error_message(SCHEMA_FETCH_FAILED, schema_url=req.schema_url),
                details={"exception": str(e)},
            ),
        )
    
    # Step 3: Flatten endpoints using processor for schema extraction
    try:
        endpoints = flatten_endpoints(openapi_doc, processor)
    except Exception as e:
        logger.exception("Error flattening endpoints: %s", e)
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_FETCH_FAILED,
                message=get_error_message(SCHEMA_FETCH_FAILED, schema_url=req.schema_url),
                details={"exception": str(e), "phase": "flattening"},
            ),
        )
    
    # Step 4: Enforce max endpoints limit
    max_endpoints = settings.SCHEMA_RAG_MAX_ENDPOINTS
    if len(endpoints) > max_endpoints:
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_TOO_LARGE,
                message=get_error_message(SCHEMA_TOO_LARGE),
                details={
                    "num_endpoints": len(endpoints),
                    "max_endpoints": max_endpoints,
                },
            ),
        )
    
    # Step 5: Create session
    ttl_minutes = req.ttl_minutes or settings.SCHEMA_RAG_DEFAULT_TTL_MINUTES
    num_endpoints = len(endpoints)
    
    try:
        session = create_session(
            schema_url=req.schema_url,
            ttl_minutes=ttl_minutes,
            num_endpoints=num_endpoints,
        )
    except Exception as e:
        logger.exception("Error creating session: %s", e)
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_FETCH_FAILED,
                message="Failed to create session",
                details={"exception": str(e)},
            ),
        )
    
    # Step 6: Initialize DB and insert endpoints
    try:
        init_session_db(session)
        insert_endpoints(session, endpoints)
    except Exception as e:
        logger.exception("Error storing endpoints: %s", e)
        return IngestResult(
            success=False,
            error=IngestError(
                error_code=SCHEMA_FETCH_FAILED,
                message="Failed to store endpoints",
                details={"exception": str(e)},
            ),
        )
    
    # Step 7: Build success response
    expires_at = session.created_at + timedelta(minutes=ttl_minutes)
    
    return IngestResult(
        success=True,
        response=IngestResponse(
            session_id=session.session_id,
            schema_url=req.schema_url,
            ttl_minutes=ttl_minutes,
            expires_at=expires_at,
            num_endpoints=num_endpoints,
        ),
    )


# ---------------------------------------------------------------------------
# Retrieval Pass Helpers
# ---------------------------------------------------------------------------


def _apply_resolved_terms_boost(
    text: str,
    resolved_terms: Optional[Dict[str, Any]],
    base_score: float,
) -> float:
    """
    Apply resolved_terms boosting to a base similarity score.
    
    Adds +0.1 per matched term found in the text (case-insensitive).
    
    Args:
        text: The endpoint text to check for term matches.
        resolved_terms: Dictionary of pre-resolved entity terms.
        base_score: The base similarity score to boost.
    
    Returns:
        Boosted score (capped at 1.0).
    """
    if not resolved_terms:
        return base_score
    
    boost = 0.0
    text_lower = text.lower()
    
    for key, value in resolved_terms.items():
        # Check if key appears in text
        if key.lower() in text_lower:
            boost += RESOLVED_TERM_BOOST
        
        # Check if value appears in text (if it's a string)
        if isinstance(value, str) and value.lower() in text_lower:
            boost += RESOLVED_TERM_BOOST
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str) and v.lower() in text_lower:
                    boost += RESOLVED_TERM_BOOST
    
    # Cap at 1.0
    return min(base_score + boost, 1.0)


def _compute_scores(
    query_embedding: np.ndarray,
    endpoint_texts: List[str],
    endpoint_ids: List[str],
    resolved_terms: Optional[Dict[str, Any]],
) -> List[EndpointScore]:
    """
    Compute similarity scores for endpoints against a query.
    
    Args:
        query_embedding: L2-normalized query embedding (1, d).
        endpoint_texts: List of endpoint text representations.
        endpoint_ids: List of endpoint IDs corresponding to texts.
        resolved_terms: Optional resolved terms for boosting.
    
    Returns:
        List of (endpoint_id, score) tuples sorted by descending score.
    """
    if not endpoint_texts:
        return []
    
    # Embed all endpoint texts
    endpoint_embeddings = embed_texts(endpoint_texts)
    
    # Compute cosine similarity (dot product of normalized vectors)
    similarities = np.dot(endpoint_embeddings, query_embedding.T).flatten()
    
    # Apply resolved_terms boost and build results
    results = []
    for idx, (eid, text) in enumerate(zip(endpoint_ids, endpoint_texts)):
        base_score = float(similarities[idx])
        boosted_score = _apply_resolved_terms_boost(text, resolved_terms, base_score)
        results.append((eid, boosted_score))
    
    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    
    return results


def _pass_1(
    session: SessionInfo,
    req: RetrieveRequest,
    query_embedding: np.ndarray,
    debug: RetrieveDebugInfo,
) -> List[EndpointScore]:
    """
    Pass 1: Score using minimal schema text only.
    
    - Loads all endpoints as MinimalAPIEndpoint from DuckDB
    - Converts each to text via .to_embedding_text()
    - Computes embeddings + cosine similarity vs. query
    - Applies resolved_terms boosting (+0.1 per matched term)
    
    Args:
        session: Session info for database access.
        req: Retrieve request with query and resolved_terms.
        query_embedding: Pre-computed query embedding.
        debug: Debug info object to update.
    
    Returns:
        List of (endpoint_id, score) sorted by descending score.
    """
    endpoints = load_all_minimal_endpoints(session)
    endpoints = _apply_endpoint_filters(endpoints, req.filter_by, req.filter_terms)

    if not endpoints:
        return []

    endpoint_texts = [ep.to_embedding_text() for ep in endpoints]
    endpoint_ids = [ep.operationId for ep in endpoints]

    return _compute_scores(
        query_embedding,
        endpoint_texts,
        endpoint_ids,
        req.resolved_terms,
    )


def _build_enriched_text(minimal: MinimalAPIEndpoint, examples: Optional[List[str]]) -> str:
    """
    Build enriched text from minimal endpoint plus examples.
    
    Args:
        minimal: MinimalAPIEndpoint object.
        examples: Optional list of example strings.
    
    Returns:
        Enriched text string.
    """
    base_text = minimal.to_embedding_text()
    
    if examples:
        examples_str = " | ".join(examples)
        return f"{base_text}\nexamples: {examples_str}"
    
    return base_text


def _pass_2(
    session: SessionInfo,
    req: RetrieveRequest,
    query_embedding: np.ndarray,
    debug: RetrieveDebugInfo,
) -> List[EndpointScore]:
    """
    Pass 2: Enriched minimal text (minimal fields + example strings).
    
    - Loads FullAPIEndpoint for each endpoint to get examples
    - Builds enriched text (minimal fields plus example strings)
    - Re-embeds and recomputes scores with boosting
    - Sets debug.used_enriched_minimal_examples = True
    
    Args:
        session: Session info for database access.
        req: Retrieve request with query and resolved_terms.
        query_embedding: Pre-computed query embedding.
        debug: Debug info object to update.
    
    Returns:
        List of (endpoint_id, score) sorted by descending score.
    """
    # Load both minimal and full endpoints
    minimal_endpoints = load_all_minimal_endpoints(session)
    full_endpoints = load_all_full_endpoints(session)

    # Apply filters to both sets consistently
    minimal_endpoints = _apply_endpoint_filters(minimal_endpoints, req.filter_by, req.filter_terms)
    full_endpoints = _apply_endpoint_filters(full_endpoints, req.filter_by, req.filter_terms)

    if not minimal_endpoints:
        return []

    # Create a map of operationId -> examples
    examples_map: Dict[str, Optional[List[str]]] = {}
    for full_ep in full_endpoints:
        examples_map[full_ep.operationId] = full_ep.examples

    # Build enriched texts
    endpoint_texts = []
    endpoint_ids = []

    for minimal_ep in minimal_endpoints:
        examples = examples_map.get(minimal_ep.operationId)
        enriched_text = _build_enriched_text(minimal_ep, examples)
        endpoint_texts.append(enriched_text)
        endpoint_ids.append(minimal_ep.operationId)
    
    # Mark that we used enriched examples
    debug.used_enriched_minimal_examples = True
    
    return _compute_scores(
        query_embedding,
        endpoint_texts,
        endpoint_ids,
        req.resolved_terms,
    )


def _pass_3(
    session: SessionInfo,
    req: RetrieveRequest,
    query_embedding: np.ndarray,
    debug: RetrieveDebugInfo,
) -> List[EndpointScore]:
    """
    Pass 3: Full schema text (parameters, simplified request_schema, examples).
    
    - Loads FullAPIEndpoint for relevant endpoints
    - Converts to text via .to_embedding_text(include_examples=True)
    - NOTE: response_schema is NOT included in the embedding text
    - Sets debug.used_fallback_full = True
    
    Args:
        session: Session info for database access.
        req: Retrieve request with query and resolved_terms.
        query_embedding: Pre-computed query embedding.
        debug: Debug info object to update.
    
    Returns:
        List of (endpoint_id, score) sorted by descending score.
    """
    full_endpoints = load_all_full_endpoints(session)
    full_endpoints = _apply_endpoint_filters(full_endpoints, req.filter_by, req.filter_terms)

    if not full_endpoints:
        return []

    endpoint_texts = [ep.to_embedding_text(include_examples=True) for ep in full_endpoints]
    endpoint_ids = [ep.operationId for ep in full_endpoints]

    # Mark that we used full fallback
    debug.used_fallback_full = True
    
    return _compute_scores(
        query_embedding,
        endpoint_texts,
        endpoint_ids,
        req.resolved_terms,
    )


# ---------------------------------------------------------------------------
# Retrieval Orchestration
# ---------------------------------------------------------------------------


def _find_session_by_schema_url(schema_url: str) -> Optional[SessionInfo]:
    """
    Find an existing non-expired session for the given schema_url.
    
    Scans all DuckDB files in SCHEMA_RAG_DUCKDB_DIR and looks for matching schema_url.
    
    Args:
        schema_url: URL of the schema to find.
    
    Returns:
        SessionInfo if found and not expired, None otherwise.
    """
    import os
    
    duckdb_dir = settings.SCHEMA_RAG_DUCKDB_DIR
    now = _now()
    
    if not os.path.isdir(duckdb_dir):
        return None
    
    for filename in os.listdir(duckdb_dir):
        if not filename.endswith('.duckdb'):
            continue
        
        session_id = filename[:-7]  # Remove '.duckdb' suffix
        
        try:
            info = load_session_info(session_id)
            if info is None:
                continue
            
            if info.schema_url == schema_url and not is_session_expired(info, now):
                return info
        except Exception:
            continue
    
    return None


def retrieve_endpoints(req: RetrieveRequest) -> RetrieveResponse:
    """
    Retrieve relevant API endpoints from an ingested schema.
    
    Workflow:
    1. Resolve session (by session_id or schema_url)
    2. Validate session exists and not expired
    3. Run multi-pass scoring (Pass 1 -> Pass 2 -> Pass 3)
    4. Apply min_score threshold (default 0.7)
    5. Apply resolved_terms boosting (+0.1 per matched term)
    6. Cap results at top_k (hard limit: SCHEMA_RAG_MAX_TOP_K)
    7. Materialize endpoints based on req.mode
    8. Build RetrieveResponse with optional debug info
    
    Args:
        req: RetrieveRequest with session_id or schema_url, query, mode, etc.
    
    Returns:
        RetrieveResponse with endpoints or error message.
    """
    now = _now()
    
    # Initialize debug info
    debug = RetrieveDebugInfo(
        used_fallback_full=False,
        used_enriched_minimal_examples=False,
        similarity_threshold_effective=req.min_score or DEFAULT_MIN_SCORE,
        num_candidates_initial=0,
        num_candidates_final=0,
        scores=None,
        error_code=None,
        error_details=None,
    )
    
    # Step 1: Resolve session
    session: Optional[SessionInfo] = None
    
    if req.session_id:
        session = load_session_info(req.session_id)
    elif req.schema_url:
        session = _find_session_by_schema_url(req.schema_url)
    
    # Step 2: Auto-ingest if session missing/expired but schema_url available
    needs_ingest = session is None or is_session_expired(session, now)
    if needs_ingest and req.schema_url:
        # Attempt auto-ingestion
        ingest_req = IngestRequest(schema_url=req.schema_url)
        ingest_result = ingest_schema(ingest_req)
        
        if ingest_result.success and ingest_result.response:
            # Load the newly created session
            session = load_session_info(ingest_result.response.session_id)
        else:
            # Return ingestion error
            error_msg = ingest_result.error.message if ingest_result.error else "Auto-ingestion failed"
            debug.error_code = ingest_result.error.error_code if ingest_result.error else SCHEMA_FETCH_FAILED
            debug.error_details = error_msg
            return RetrieveResponse(
                session_id="",
                mode=req.mode,
                message=error_msg,
                debug=debug if req.include_debug else None,
            )
    
    # Step 3: Validate session (returns error only if no schema_url was available for re-ingestion)
    if session is None:
        debug.error_code = SESSION_MISSING_OR_EXPIRED
        return RetrieveResponse(
            session_id=req.session_id or "",
            mode=req.mode,
            message=get_error_message(SESSION_MISSING_OR_EXPIRED),
            debug=debug if req.include_debug else None,
        )
    
    if is_session_expired(session, now):
        debug.error_code = SESSION_MISSING_OR_EXPIRED
        return RetrieveResponse(
            session_id=session.session_id,
            mode=req.mode,
            message=get_error_message(SESSION_MISSING_OR_EXPIRED),
            debug=debug if req.include_debug else None,
        )
    
    # Compute query embedding once
    query_embedding = embed_texts([req.query])
    if query_embedding.size == 0:
        debug.error_code = EMBEDDING_FAILED
        return RetrieveResponse(
            session_id=session.session_id,
            mode=req.mode,
            message=get_error_message(EMBEDDING_FAILED),
            debug=debug if req.include_debug else None,
        )
    
    # Step 3: Run multi-pass scoring
    min_score = req.min_score if req.min_score is not None else DEFAULT_MIN_SCORE
    debug.similarity_threshold_effective = min_score
    
    # Effective top_k with hard cap
    if req.top_k == "ALL":
        top_k = settings.SCHEMA_RAG_MAX_ENDPOINTS
    else:
        top_k = min(req.top_k, settings.SCHEMA_RAG_MAX_TOP_K)
    
    # Pass 1: Minimal schema text
    scores = _pass_1(session, req, query_embedding, debug)
    debug.num_candidates_initial = len(scores)
    
    # Filter by min_score and take top_k
    passing_scores = [(eid, score) for eid, score in scores if score >= min_score]
    
    # If Pass 1 has enough good matches, use them
    if len(passing_scores) >= 1:
        final_scores = passing_scores[:top_k]
    else:
        # Pass 2: Enriched minimal with examples
        scores = _pass_2(session, req, query_embedding, debug)
        passing_scores = [(eid, score) for eid, score in scores if score >= min_score]
        
        if len(passing_scores) >= 1:
            final_scores = passing_scores[:top_k]
        else:
            # Pass 3: Full schema text
            scores = _pass_3(session, req, query_embedding, debug)
            passing_scores = [(eid, score) for eid, score in scores if score >= min_score]
            final_scores = passing_scores[:top_k]
    
    debug.num_candidates_final = len(final_scores)
    
    # Step 4: Handle no good matches
    if not final_scores:
        debug.error_code = NO_GOOD_MATCH
        return RetrieveResponse(
            session_id=session.session_id,
            mode=req.mode,
            message=get_error_message(NO_GOOD_MATCH),
            debug=debug if req.include_debug else None,
        )
    
    # Populate debug scores
    debug.scores = [score for _, score in final_scores]
    
    # Step 5: Materialize endpoints based on mode
    selected_ids = [eid for eid, _ in final_scores]
    
    if req.mode == "minimal":
        endpoints = load_minimal_endpoints_by_ids(session, selected_ids)
        # Preserve order from scoring
        id_to_endpoint = {ep.operationId: ep for ep in endpoints}
        ordered_endpoints = [id_to_endpoint[eid] for eid in selected_ids if eid in id_to_endpoint]
        
        return RetrieveResponse(
            session_id=session.session_id,
            mode="minimal",
            endpoints_minimal=ordered_endpoints,
            debug=debug if req.include_debug else None,
        )
    else:
        endpoints = load_full_endpoints_by_ids(session, selected_ids)
        # Preserve order from scoring
        id_to_endpoint = {ep.operationId: ep for ep in endpoints}
        ordered_endpoints = [id_to_endpoint[eid] for eid in selected_ids if eid in id_to_endpoint]
        
        return RetrieveResponse(
            session_id=session.session_id,
            mode="full",
            endpoints_full=ordered_endpoints,
            debug=debug if req.include_debug else None,
        )
