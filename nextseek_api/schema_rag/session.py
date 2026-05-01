"""
Session lifecycle management for Schema RAG.

This module provides functionality for:
- Generating session IDs
- Creating and loading session metadata from DuckDB
- Checking session expiration
- Cleaning up expired sessions

Implementation: Phase 3
"""

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import duckdb
from django.conf import settings

from .models import SessionInfo


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def generate_session_id() -> str:
    """Return a new opaque session id (uuid4 hex)."""
    return uuid.uuid4().hex


def _now() -> datetime:
    """Return timezone-aware 'now' compatible with SessionInfo."""
    return datetime.now(timezone.utc)


def get_db_path_for_session(session_id: str) -> str:
    """Return absolute path to DuckDB file for this session."""
    return os.path.join(settings.SCHEMA_RAG_DUCKDB_DIR, f"{session_id}.duckdb")


# ---------------------------------------------------------------------------
# Session Expiration
# ---------------------------------------------------------------------------


def is_session_expired(info: SessionInfo, now: datetime) -> bool:
    """
    Return True if now > info.created_at + ttl_minutes.
    
    Args:
        info: SessionInfo containing created_at and ttl_minutes.
        now: Current datetime (should be timezone-aware).
    
    Returns:
        True if the session has expired, False otherwise.
    """
    expires_at = info.created_at + timedelta(minutes=info.ttl_minutes)
    return now > expires_at


# ---------------------------------------------------------------------------
# Core Session Lifecycle
# ---------------------------------------------------------------------------


def create_session(schema_url: str, ttl_minutes: int, num_endpoints: int) -> SessionInfo:
    """
    Create a new session_id, DuckDB file, and session_meta row.
    
    Args:
        schema_url: URL of the ingested OpenAPI schema.
        ttl_minutes: Session TTL in minutes.
        num_endpoints: Number of endpoints indexed.
    
    Returns:
        SessionInfo with all session metadata.
    
    Raises:
        duckdb.Error: If DuckDB operations fail.
    """
    session_id = generate_session_id()
    db_path = get_db_path_for_session(session_id)
    created_at = _now()
    
    # Create DuckDB file and session_meta table
    conn = duckdb.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_meta (
                session_id VARCHAR PRIMARY KEY,
                created_at TIMESTAMP,
                ttl_minutes INTEGER,
                schema_url VARCHAR,
                num_endpoints INTEGER
            )
        """)
        
        conn.execute(
            """
            INSERT INTO session_meta (session_id, created_at, ttl_minutes, schema_url, num_endpoints)
            VALUES (?, ?, ?, ?, ?)
            """,
            [session_id, created_at, ttl_minutes, schema_url, num_endpoints]
        )
    finally:
        conn.close()
    
    return SessionInfo(
        session_id=session_id,
        db_path=db_path,
        created_at=created_at,
        ttl_minutes=ttl_minutes,
        schema_url=schema_url,
        num_endpoints=num_endpoints
    )


def load_session_info(session_id: str) -> Optional[SessionInfo]:
    """
    Open the DuckDB file for session_id, read session_meta, and return SessionInfo.
    
    Args:
        session_id: The session identifier.
    
    Returns:
        SessionInfo if the session exists and has valid metadata, None otherwise.
    """
    db_path = get_db_path_for_session(session_id)
    
    # Check if file exists
    if not os.path.exists(db_path):
        return None
    
    try:
        conn = duckdb.connect(db_path, read_only=True)
        try:
            result = conn.execute(
                "SELECT session_id, created_at, ttl_minutes, schema_url, num_endpoints FROM session_meta LIMIT 1"
            ).fetchone()
            
            if result is None:
                return None
            
            row_session_id, created_at, ttl_minutes, schema_url, num_endpoints = result
            
            # Ensure created_at is timezone-aware
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            
            return SessionInfo(
                session_id=row_session_id,
                db_path=db_path,
                created_at=created_at,
                ttl_minutes=ttl_minutes,
                schema_url=schema_url,
                num_endpoints=num_endpoints
            )
        finally:
            conn.close()
    except duckdb.Error as e:
        logger.warning("Failed to load session %s: %s", session_id, e)
        return None


def delete_session(session_id: str) -> None:
    """
    Delete the DuckDB file for this session_id if it exists.
    
    Silently ignores missing files.
    
    Args:
        session_id: The session identifier.
    """
    db_path = get_db_path_for_session(session_id)
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass  # Ignore missing files


def cleanup_expired_sessions() -> None:
    """
    Iterate DuckDB files under SCHEMA_RAG_DUCKDB_DIR and delete those whose session_meta is expired.
    
    This function logs warnings for files that cannot be processed but continues
    with remaining files.
    """
    duckdb_dir = settings.SCHEMA_RAG_DUCKDB_DIR
    now = _now()
    
    if not os.path.isdir(duckdb_dir):
        return
    
    for filename in os.listdir(duckdb_dir):
        if not filename.endswith('.duckdb'):
            continue
        
        # Extract session_id from filename
        session_id = filename[:-7]  # Remove '.duckdb' suffix
        
        try:
            info = load_session_info(session_id)
            if info is None:
                # Cannot load session info - delete orphaned file
                logger.info("Deleting orphaned session file: %s", filename)
                delete_session(session_id)
            elif is_session_expired(info, now):
                logger.info("Deleting expired session: %s", session_id)
                delete_session(session_id)
        except Exception as e:
            logger.warning("Failed to process session file %s during cleanup: %s", filename, e)
            continue

