"""
DuckDB integration for Schema RAG.

This module provides functionality for:
- Initializing per-session DuckDB databases
- Creating endpoints and session_meta tables
- Inserting ingested endpoints
- Loading endpoints for retrieval (minimal and full)

Implementation: Phase 4
"""

import json
import logging
from typing import List, Optional

import duckdb

from .models import SessionInfo, MinimalAPIEndpoint, FullAPIEndpoint


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Table Initialization
# ---------------------------------------------------------------------------


def init_session_db(session: SessionInfo) -> None:
    """
    Create DuckDB file and both session_meta and endpoints tables if not already present.
    
    This function ensures both tables exist in the session's DuckDB file and
    inserts the session_meta row if not already present.
    
    Args:
        session: SessionInfo containing the db_path and session metadata.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    conn = duckdb.connect(session.db_path)
    try:
        # Create session_meta table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_meta (
                session_id VARCHAR PRIMARY KEY,
                created_at TIMESTAMP,
                ttl_minutes INTEGER,
                schema_url VARCHAR,
                num_endpoints INTEGER
            )
        """)
        
        # Insert session_meta row if not already present
        conn.execute(
            """
            INSERT INTO session_meta (session_id, created_at, ttl_minutes, schema_url, num_endpoints)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (session_id) DO NOTHING
            """,
            [session.session_id, session.created_at, session.ttl_minutes,
             session.schema_url, session.num_endpoints]
        )
        
        # Create endpoints table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS endpoints (
                endpoint_id VARCHAR PRIMARY KEY,
                operationId VARCHAR,
                method VARCHAR,
                tags VARCHAR[],
                description VARCHAR,
                path VARCHAR,
                parameters JSON,
                request_schema JSON,
                response_schema JSON,
                examples VARCHAR[]
            )
        """)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoint Insertion
# ---------------------------------------------------------------------------


def insert_endpoints(session: SessionInfo, endpoints: List[FullAPIEndpoint]) -> int:
    """
    Bulk-insert endpoints into the session's DuckDB.
    
    Args:
        session: SessionInfo containing the db_path for the DuckDB file.
        endpoints: List of FullAPIEndpoint objects to insert.
    
    Returns:
        Count of inserted endpoints.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    if not endpoints:
        return 0
    
    conn = duckdb.connect(session.db_path)
    try:
        # Prepare data for bulk insert
        rows = []
        for ep in endpoints:
            rows.append((
                ep.operationId,  # endpoint_id = operationId
                ep.operationId,
                ep.method,
                ep.tags,  # DuckDB handles Python list -> VARCHAR[]
                ep.description,
                ep.path,
                json.dumps(ep.parameters) if ep.parameters else None,
                json.dumps(ep.request_schema) if ep.request_schema else None,
                json.dumps(ep.response_schema) if ep.response_schema else None,
                ep.examples,  # DuckDB handles Python list -> VARCHAR[]
            ))
        
        conn.executemany(
            """
            INSERT INTO endpoints (
                endpoint_id, operationId, method, tags, description,
                path, parameters, request_schema, response_schema, examples
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows
        )
        
        return len(rows)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoint Loading - Minimal
# ---------------------------------------------------------------------------


def load_all_minimal_endpoints(session: SessionInfo) -> List[MinimalAPIEndpoint]:
    """
    Read endpoints table and return MinimalAPIEndpoint list.
    
    Used for scoring passes (Pass 1/2/3) to build embedding texts.
    
    Args:
        session: SessionInfo containing the db_path for the DuckDB file.
    
    Returns:
        List of MinimalAPIEndpoint objects.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    conn = duckdb.connect(session.db_path, read_only=True)
    try:
        result = conn.execute(
            "SELECT operationId, method, tags, description, path FROM endpoints"
        ).fetchall()
        
        endpoints = []
        for row in result:
            operationId, method, tags, description, path = row
            endpoints.append(MinimalAPIEndpoint(
                operationId=operationId,
                method=method,
                tags=list(tags) if tags else None,
                description=description,
                path=path,
            ))
        
        return endpoints
    finally:
        conn.close()


def load_minimal_endpoints_by_ids(
    session: SessionInfo,
    endpoint_ids: List[str]
) -> List[MinimalAPIEndpoint]:
    """
    Return MinimalAPIEndpoint objects for given endpoint_ids.
    
    Provides symmetric minimal-only materialization path.
    
    Args:
        session: SessionInfo containing the db_path for the DuckDB file.
        endpoint_ids: List of endpoint IDs to retrieve.
    
    Returns:
        List of MinimalAPIEndpoint objects.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    if not endpoint_ids:
        return []
    
    conn = duckdb.connect(session.db_path, read_only=True)
    try:
        # Use parameterized query with list
        placeholders = ", ".join(["?" for _ in endpoint_ids])
        query = f"""
            SELECT operationId, method, tags, description, path
            FROM endpoints
            WHERE endpoint_id IN ({placeholders})
        """
        
        result = conn.execute(query, endpoint_ids).fetchall()
        
        endpoints = []
        for row in result:
            operationId, method, tags, description, path = row
            endpoints.append(MinimalAPIEndpoint(
                operationId=operationId,
                method=method,
                tags=list(tags) if tags else None,
                description=description,
                path=path,
            ))
        
        return endpoints
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoint Loading - Full
# ---------------------------------------------------------------------------


def load_all_full_endpoints(session: SessionInfo) -> List[FullAPIEndpoint]:
    """
    Return FullAPIEndpoint list for all rows.
    
    NOTE:
    - response_schema is stored in DuckDB for completeness,
      but is NOT exposed to the LLM in RetrieveResponse.
    - When constructing FullAPIEndpoint objects for outward responses,
      response_schema is set to None.
    
    Args:
        session: SessionInfo containing the db_path for the DuckDB file.
    
    Returns:
        List of FullAPIEndpoint objects with response_schema=None.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    conn = duckdb.connect(session.db_path, read_only=True)
    try:
        result = conn.execute(
            """
            SELECT operationId, method, tags, description, path,
                   parameters, request_schema, examples
            FROM endpoints
            """
        ).fetchall()
        
        endpoints = []
        for row in result:
            (operationId, method, tags, description, path,
             parameters, request_schema, examples) = row
            
            endpoints.append(FullAPIEndpoint(
                operationId=operationId,
                method=method,
                tags=list(tags) if tags else None,
                description=description,
                path=path,
                parameters=json.loads(parameters) if parameters else None,
                request_schema=json.loads(request_schema) if request_schema else {},
                response_schema=None,  # Excluded from LLM responses
                examples=list(examples) if examples else None,
            ))
        
        return endpoints
    finally:
        conn.close()


def load_full_endpoints_by_ids(
    session: SessionInfo,
    endpoint_ids: List[str]
) -> List[FullAPIEndpoint]:
    """
    Return FullAPIEndpoint objects for given endpoint_ids.
    
    Used for final materialization after scoring - only loads
    the endpoints whose IDs were selected.
    
    NOTE:
    - response_schema is NOT included when returning to callers.
    
    Args:
        session: SessionInfo containing the db_path for the DuckDB file.
        endpoint_ids: List of endpoint IDs to retrieve.
    
    Returns:
        List of FullAPIEndpoint objects with response_schema=None.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    if not endpoint_ids:
        return []
    
    conn = duckdb.connect(session.db_path, read_only=True)
    try:
        # Use parameterized query with list
        placeholders = ", ".join(["?" for _ in endpoint_ids])
        query = f"""
            SELECT operationId, method, tags, description, path,
                   parameters, request_schema, examples
            FROM endpoints
            WHERE endpoint_id IN ({placeholders})
        """
        
        result = conn.execute(query, endpoint_ids).fetchall()
        
        endpoints = []
        for row in result:
            (operationId, method, tags, description, path,
             parameters, request_schema, examples) = row
            
            endpoints.append(FullAPIEndpoint(
                operationId=operationId,
                method=method,
                tags=list(tags) if tags else None,
                description=description,
                path=path,
                parameters=json.loads(parameters) if parameters else None,
                request_schema=json.loads(request_schema) if request_schema else {},
                response_schema=None,  # Excluded from LLM responses
                examples=list(examples) if examples else None,
            ))
        
        return endpoints
    finally:
        conn.close()
