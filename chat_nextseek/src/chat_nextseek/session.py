import mysql.connector
import sqlite3
import json
from typing import Any

class SessionState:
    def get(self, key, default=None):
        """Return a session value or the provided default when the key is missing."""
        raise NotImplementedError

    def __getitem__(self, key):
        """Dictionary-style lookup for session values."""
        raise NotImplementedError

    def __setitem__(self, key, value):
        """Dictionary-style assignment for session values."""
        raise NotImplementedError

class SQLSessionState(SessionState):
    def _convert_query(self, query: str) -> str:
        """Translate placeholder style between SQLite (`?`) and MySQL (`%s`)."""
        if self.paramstyle == "format":
            return query.replace("?", "%s")
        elif self.paramstyle == "qmark":
            return query.replace("%s", "?")
        return query

    def get(self, key: str, default: Any = None) -> Any:
        """Load and JSON-decode a persisted session value from the backing SQL store."""
        query = "SELECT value FROM session_state WHERE session_id = ? AND `key` = ?"
        self._c.execute(
            self._convert_query(query),
            (self.session_id, key))
        row = self._c.fetchone()

        if row is None:
            return default

        return json.loads(row[0])

    def __getitem__(self, key: str) -> Any:
        """Return a required session value, raising KeyError when absent."""
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __del__(self):
        """Best-effort cleanup for the underlying DB connection."""
        try:
            self._conn.commit()
            self._conn.close()
        except:
            print("Could not close database connection.")

class SQLiteSessionState(SQLSessionState):
    def __init__(self, db_path: str, session_id: str):
        """Initialize a SQLite-backed session store and ensure its parent directory exists."""
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.session_id = session_id
        self._conn = self._conn()
        self._c = self._conn.cursor()
        self.paramstyle = "qmark"
        self._init_db()
    
    def _init_db(self):
        """Create the SQLite session table when it does not already exist."""
        self._c.execute("""
                CREATE TABLE IF NOT EXISTS session_state (
                    session_id TEXT NOT NULL,
                    `key` TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (session_id, `key`)
                )
            """)
        self._conn.commit()
            
    def _conn(self):
        """Open a SQLite connection for this session store."""
        return sqlite3.connect(self.db_path)

    def __setitem__(self, key: str, value: Any):
        """Persist a JSON-serializable session value in SQLite."""
        self._c.execute(
                """
                INSERT INTO session_state (session_id, `key`, value)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, `key`)
                DO UPDATE SET value = excluded.value
                """,
                (self.session_id, key, json.dumps(value)),
            )
        self._conn.commit()

class MySQLSessionState(SQLSessionState):
    def __init__(self, db_config: dict, session_id: str):
        """Initialize a MySQL-backed session store from a connection config dict."""
        self.db_config = db_config
        self.session_id = session_id
        self.paramstyle = "format"
        self._conn = self._conn()
        self._c = self._conn.cursor()

    def __setitem__(self, key: str, value: Any):
        """Persist a JSON-serializable session value in MySQL."""
        self._c.execute(
                """
                INSERT INTO session_state (session_id, `key`, value)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                value = VALUES(value)
                """,
                (self.session_id, key, json.dumps(value)),
            )
        self._conn.commit()

    def _conn(self):
        """Open a MySQL connection using the configured credentials."""
        user = self.db_config['user']
        password = self.db_config['password']
        host = self.db_config['host']
        port = self.db_config['port']
        database = self.db_config['database']
        
        return mysql.connector.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database
                )
