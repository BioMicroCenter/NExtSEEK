from __future__ import annotations

import hashlib
import fcntl
import json
import os
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import MySQLdb
import orjson
from django.conf import settings
from django.db import connections
from kombu import Connection

DENYLIST = {"dmac", "seek_production", "test_dmac"}


class InjectedAttributeFault(RuntimeError):
    pass


@dataclass(frozen=True)
class SqlTelemetrySnapshot:
    sql_count: int
    maximum_lock_wait_seconds: float
    maximum_packet_bytes: int
    timeouts: int


class _TelemetryCursor:
    def __init__(self, cursor, owner, token): self._cursor, self._owner, self._token = cursor, owner, token
    def __getattr__(self, name): return getattr(self._cursor, name)
    def execute(self, sql, params=()):
        return self._owner.observe(self._token, self._cursor.execute, sql, params, many=False)
    def executemany(self, sql, params):
        return self._owner.observe(self._token, self._cursor.executemany, sql, params, many=True)


class _TelemetryConnection:
    def __init__(self, connection, owner, token):
        self._connection, self._owner, self._token = connection, owner, token
    def __getattr__(self, name): return getattr(self._connection, name)
    def cursor(self, *args, **kwargs): return _TelemetryCursor(self._connection.cursor(*args, **kwargs), self._owner, self._token)
    def close(self):
        self._owner.finish(self._connection, self._token)
        return self._connection.close()


class SqlTelemetryConsumerLease:
    """The lane parent alone owns the global consumer transition/restoration."""
    def __init__(self, database, marker):
        self.database, self.marker = database, Path(marker)
        lock = Path("/tmp") / ("attribute-sql-telemetry-" +
                               database.server_identity["server_uuid"] + ".lock")
        self.lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        connection = MySQLdb.connect(db="performance_schema", **database._connection_kwargs)
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT NAME,ENABLED FROM setup_consumers WHERE NAME IN "
                           "('events_statements_current','events_statements_history_long')")
            prior = dict(cursor.fetchall())
            cursor.execute("UPDATE setup_consumers SET ENABLED='YES' WHERE NAME IN "
                           "('events_statements_current','events_statements_history_long')")
            connection.commit()
        finally:
            connection.close()
        payload = {"schema_version": "attribute-sql-telemetry-lease/v1", "owner_pid": os.getpid(),
                   "server_uuid": database.server_identity["server_uuid"],
                   "prior_consumers": prior, "refcount": 1}
        descriptor = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.environ["ATTRIBUTE_SQL_TELEMETRY_LANE_MARKER"] = str(self.marker)

    def close(self):
        payload = orjson.loads(self.marker.read_bytes())
        if payload["owner_pid"] != os.getpid() or payload["refcount"] != 1:
            raise RuntimeError("telemetry consumer lease ownership changed")
        connection = MySQLdb.connect(db="performance_schema", **self.database._connection_kwargs)
        try:
            cursor = connection.cursor()
            for name, enabled in sorted(payload["prior_consumers"].items()):
                cursor.execute("UPDATE setup_consumers SET ENABLED=%s WHERE NAME=%s", (enabled, name))
            connection.commit()
            cursor.execute("SELECT NAME,ENABLED FROM setup_consumers WHERE NAME IN "
                           "('events_statements_current','events_statements_history_long')")
            if dict(cursor.fetchall()) != payload["prior_consumers"]:
                raise RuntimeError("lane telemetry consumer restoration failed")
        finally:
            connection.close()
        self.marker.unlink()
        os.environ.pop("ATTRIBUTE_SQL_TELEMETRY_LANE_MARKER", None)
        fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        os.close(self.lock_fd)


class SqlTelemetry:
    """Fail-closed MariaDB telemetry
    no client duration is treated as lock wait."""
    _STATUS = ("Bytes_received", "Bytes_sent")

    def __init__(self, database):
        self._database = database
        self._preflight()
        self.reset()

    def _admin(self):
        return MySQLdb.connect(db="performance_schema", **self._database._connection_kwargs)

    def _preflight(self):
        connection = self._admin()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT @@performance_schema, @@performance_schema_events_statements_history_long_size")
            enabled, history_size = cursor.fetchone()
            cursor.execute("SELECT NAME,ENABLED FROM setup_consumers WHERE NAME IN ('events_statements_current','events_statements_history_long')")
            consumers = dict(cursor.fetchall())
            marker = os.environ.get("ATTRIBUTE_SQL_TELEMETRY_LANE_MARKER")
            self._lane_managed = bool(marker)
            self._prior_consumers = None if marker else dict(consumers)
            if marker:
                lease = orjson.loads(Path(marker).read_bytes())
                if (lease.get("schema_version") != "attribute-sql-telemetry-lease/v1"
                        or lease.get("server_uuid") != self._database.server_identity["server_uuid"]
                        or lease.get("refcount") != 1
                        or consumers != {"events_statements_current": "YES",
                                         "events_statements_history_long": "YES"}):
                    raise RuntimeError("child telemetry requires active lane consumer lease")
            elif consumers.get("events_statements_history_long") != "YES":
                cursor.execute(
                    "UPDATE setup_consumers SET ENABLED='YES' "
                    "WHERE NAME='events_statements_history_long'"
                )
                connection.commit()
                cursor.execute("SELECT NAME,ENABLED FROM setup_consumers WHERE NAME IN ('events_statements_current','events_statements_history_long')")
                consumers = dict(cursor.fetchall())
            cursor.execute("SELECT COUNT(*) FROM setup_instruments WHERE NAME LIKE 'statement/%' AND ENABLED='YES' AND TIMED='YES'")
            timed = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='performance_schema' AND table_name='events_statements_history_long' AND column_name IN ('THREAD_ID','EVENT_ID','LOCK_TIME','SQL_TEXT','MYSQL_ERRNO')")
            columns = cursor.fetchone()[0]
            if (not enabled or int(history_size) < 1 or timed < 1 or columns != 5
                    or consumers != {"events_statements_current": "YES", "events_statements_history_long": "YES"}):
                raise RuntimeError("MariaDB server statement telemetry is unavailable")
        finally:
            connection.close()

    def close(self):
        if self._open:
            raise RuntimeError("cannot restore telemetry consumer with unfinished connections")
        if self._lane_managed:
            return
        connection = self._admin()
        try:
            cursor = connection.cursor()
            for name, enabled in sorted(self._prior_consumers.items()):
                cursor.execute("UPDATE setup_consumers SET ENABLED=%s WHERE NAME=%s", (enabled, name))
            connection.commit()
            cursor.execute("SELECT NAME,ENABLED FROM setup_consumers WHERE NAME IN ('events_statements_current','events_statements_history_long')")
            if dict(cursor.fetchall()) != self._prior_consumers:
                raise RuntimeError("MariaDB telemetry consumer restoration failed")
        finally:
            connection.close()

    def reset(self):
        if getattr(self, "_open", None):
            raise RuntimeError("cannot reset with unfinished telemetry connections")
        self._open = {}
        self._rows = []
        self._next_token = 0

    @staticmethod
    def _session_bytes(cursor):
        cursor.execute("SHOW SESSION STATUS WHERE Variable_name IN ('Bytes_received','Bytes_sent')")
        values = {str(name): int(value) for name, value in cursor.fetchall()}
        if set(values) != set(SqlTelemetry._STATUS):
            raise RuntimeError("MariaDB session byte counters are unavailable")
        return values

    def wrap(self, connection):
        return _TelemetryConnection(connection, self, self._register(connection))

    def _register(self, connection):
        cursor = connection.cursor()
        cursor.execute("SELECT CONNECTION_ID()")
        process_id = cursor.fetchone()[0]
        cursor.execute("SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID=%s", (process_id,))
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("MariaDB telemetry thread mapping unavailable")
        thread_id = row[0]
        cursor.execute("SELECT COALESCE(MAX(EVENT_ID),0) FROM performance_schema.events_statements_history_long WHERE THREAD_ID=%s", (thread_id,))
        start_event = cursor.fetchone()[0]
        baseline_bytes = self._session_bytes(cursor)
        token = self._next_token
        self._next_token += 1
        self._open[token] = {"thread_id": thread_id, "start_event": start_event,
                             "baseline_bytes": baseline_bytes, "expected_markers": []}
        return token

    @contextmanager
    def wrap_django_connection(self, alias):
        if alias not in connections.databases:
            raise RuntimeError(f"unknown disposable Django alias: {alias}")
        django_connection = connections[alias]
        django_connection.ensure_connection()
        raw_connection = django_connection.connection
        token = self._register(raw_connection)

        def instrument(execute, sql, params, many, context):
            return self.observe(token, execute, sql, params, many=many)

        try:
            with django_connection.execute_wrapper(instrument):
                yield django_connection
        finally:
            if django_connection.connection is not raw_connection:
                raise RuntimeError("Django reconnected during a telemetry interval")
            self.finish(raw_connection, token)

    def observe(self, token, operation, sql, params, *, many):
        if token not in self._open:
            raise RuntimeError("unregistered telemetry connection")
        state = self._open[token]
        marker = f"/*attribute-telemetry-{token}-{len(state['expected_markers'])}*/"
        state["expected_markers"].append(marker)
        tagged = marker + " " + sql if isinstance(sql, str) else marker.encode() + b" " + bytes(sql)
        return operation(tagged, params)

    def finish(self, connection, token):
        state = self._open.pop(token)
        ending_bytes = self._session_bytes(connection.cursor())
        admin = self._admin()
        try:
            cursor = admin.cursor()
            cursor.execute("SELECT EVENT_ID,LOCK_TIME,SQL_TEXT,MYSQL_ERRNO FROM events_statements_history_long WHERE THREAD_ID=%s AND EVENT_ID>%s ORDER BY EVENT_ID", (state["thread_id"], state["start_event"]))
            rows = [row for row in cursor.fetchall()
                    if row[2] and str(row[2]).startswith("/*attribute-telemetry-")]
        finally:
            admin.close()
        observed_markers = [str(row[2]).split("*/", 1)[0] + "*/" for row in rows]
        if observed_markers != state["expected_markers"]:
            raise RuntimeError(f"MariaDB statement history incomplete: expected {len(state['expected_markers'])}, observed {len(rows)}")
        packet_bytes = max(ending_bytes[name] - state["baseline_bytes"][name] for name in self._STATUS)
        self._rows.append((rows, packet_bytes))

    def snapshot(self):
        if self._open:
            raise RuntimeError("telemetry connection must close before snapshot")
        statements = [row for rows, _ in self._rows for row in rows]
        if not statements:
            raise RuntimeError("MariaDB produced no server statement telemetry")
        value = SqlTelemetrySnapshot(
            sql_count=len(statements),
            maximum_lock_wait_seconds=max(int(row[1] or 0) for row in statements) / 1_000_000_000_000,
            maximum_packet_bytes=max(packet for _, packet in self._rows),
            timeouts=sum(int(row[3] or 0) in {1205, 3024} for row in statements),
        )
        self.reset()
        return value


@dataclass
class RailsLikeWorkloadHandle:
    stop_event: threading.Event
    thread: threading.Thread
    failures: list[BaseException]


class RailsLikeWorkload:
    def start(self, database, *, sample_type_id, shard=None):
        if shard is not None:
            database._assert_owned_shard(shard)
        target_database = database.database_name if shard is None else shard.database_name
        stop_event, failures = threading.Event(), []
        def run():
            connection = MySQLdb.connect(db=target_database, **database._connection_kwargs)
            try:
                while not stop_event.is_set():
                    cursor = connection.cursor()
                    cursor.execute("SELECT id,json_metadata FROM samples WHERE sample_type_id=%s ORDER BY id LIMIT 25", (sample_type_id,))
                    cursor.fetchall()
                    cursor.execute("UPDATE samples SET updated_at=updated_at WHERE sample_type_id<>%s ORDER BY id LIMIT 1", (sample_type_id,))
                    connection.commit()
            except BaseException as exc:
                failures.append(exc)
            finally:
                connection.close()
        thread = threading.Thread(target=run, name=f"rails-like-{sample_type_id}", daemon=True)
        thread.start()
        return RailsLikeWorkloadHandle(stop_event, thread, failures)
    def stop(self, handle):
        handle.stop_event.set()
        handle.thread.join(timeout=30)
        if handle.thread.is_alive():
            raise TimeoutError("Rails-like workload did not stop")
        if handle.failures:
            raise RuntimeError("Rails-like workload failed") from handle.failures[0]


@dataclass
class AttributeFaultController:
    frozen_points: set[str]
    control_path: Path | None = None

    def __post_init__(self):
        if self.control_path is None:
            self.control_path = Path(os.environ["ATTRIBUTE_TEST_FAULT_CONTROL"])
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.control_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"armed": {}, "observed": {}, "events": []}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def from_environment(cls, frozen_points):
        return cls(set(frozen_points), Path(os.environ["ATTRIBUTE_TEST_FAULT_CONTROL"]))

    def _transaction(self, mutate):
        with self.control_path.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            state = json.load(stream)
            result = mutate(state)
            stream.seek(0)
            stream.truncate()
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream, fcntl.LOCK_UN)
            return result

    def arm(self, point: str, hits: int = 1) -> None:
        if point not in self.frozen_points:
            raise ValueError(f"unknown frozen fault point: {point}")
        if hits < 1:
            raise ValueError("hits must be positive")
        self._transaction(lambda state: state["armed"].__setitem__(point, hits))

    def hit(self, point: str) -> None:
        if point not in self.frozen_points:
            raise ValueError(f"unknown frozen fault point: {point}")
        def claim(state):
            state["observed"][point] = state["observed"].get(point, 0) + 1
            remaining = state["armed"].get(point, 0)
            if remaining:
                state["armed"][point] = remaining - 1
                state["events"].append({"point": point, "hit": state["observed"][point],
                                        "pid": os.getpid(), "observed_at": time.time()})
            return remaining > 0
        if self._transaction(claim):
            raise InjectedAttributeFault(point)

    def observed(self, point: str) -> int:
        return self._transaction(lambda state: state["observed"].get(point, 0))

    def clear(self) -> None:
        def reset(state):
            state.update(armed={}, observed={}, events=[])
        self._transaction(reset)


@dataclass(frozen=True)
class SeedTemplate:
    checksum: str
    logical_seed_sha256: str
    semantic_state_sha256: str
    statements: tuple[tuple[str, tuple], ...]


@dataclass(frozen=True)
class DatabaseShard:
    shard_id: str
    database_name: str
    owner_database_uuid: str
    template_checksum: str
    django_alias: str


@dataclass
class DisposableAttributeDatabase:
    django_alias: str
    server_identity: dict
    database_uuid: str
    database_name: str
    _connection_kwargs: dict
    _admin_database: str = "mysql"
    _torn_down: bool = False
    _previous_seek_alias: str | None = None
    _alias_installed: bool = False
    _seed_statements: list[tuple[str, tuple]] = field(default_factory=list)
    _template: SeedTemplate | None = None
    _shards: dict[str, DatabaseShard] = field(default_factory=dict)
    _installed_shard_aliases: set[str] = field(default_factory=set)
    _created_shard_names: set[str] = field(default_factory=set)
    _owns_base: bool = True

    @classmethod
    def from_environment(cls) -> "DisposableAttributeDatabase":
        db_uuid = os.environ.get("ATTRIBUTE_TEST_DISPOSABLE_DB_UUID") or str(uuid.uuid4())
        generated = f"attribute_test_{db_uuid.replace('-', '')[:12]}"
        name = os.environ.get("ATTRIBUTE_TEST_DATABASE_NAME", generated)
        if name in DENYLIST or not name.startswith("attribute_test_"):
            raise RuntimeError(f"denylisted or non-disposable database name: {name}")
        kwargs = {
            "host": os.environ["ATTRIBUTE_TEST_DB_HOST"],
            "port": int(os.environ.get("ATTRIBUTE_TEST_DB_PORT", "3306")),
            "user": os.environ["ATTRIBUTE_TEST_DB_USER"],
            "passwd": os.environ["ATTRIBUTE_TEST_DB_PASSWORD"],
            "charset": "utf8mb4",
        }
        admin = None
        last_error = None
        for _ in range(120):
            try:
                admin = MySQLdb.connect(db="mysql", **kwargs)
                break
            except MySQLdb.OperationalError as exc:
                last_error = exc
                if exc.args and exc.args[0] not in (2002, 2003, 2013):
                    raise
                time.sleep(0.25)
        if admin is None:
            raise last_error or RuntimeError("disposable database never became reachable")
        try:
            cursor = admin.cursor()
            cursor.execute("SELECT @@server_uuid, @@hostname, @@port, VERSION()")
            server_uuid, hostname, port, version = cursor.fetchone()
            if os.environ.get("ATTRIBUTE_TEST_DATABASE_PRECREATED") == "1":
                cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", [name])
                if cursor.fetchone()[0] != 1:
                    raise RuntimeError("precreated disposable database is missing")
            else:
                cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                admin.commit()
        finally:
            admin.close()
        identity = {"server_uuid": str(server_uuid), "hostname": str(hostname), "port": int(port), "version": str(version), "database_name": name}
        database = cls("attribute_disposable", identity, db_uuid, name, kwargs,
                       _owns_base=os.environ.get("ATTRIBUTE_TEST_DATABASE_PRECREATED") != "1")
        database.install_django_alias()
        return database

    def _django_config(self, database_name):
        return {
            "ENGINE": "django.db.backends.mysql", "NAME": database_name,
            "HOST": self._connection_kwargs["host"], "PORT": self._connection_kwargs["port"],
            "USER": self._connection_kwargs["user"], "PASSWORD": self._connection_kwargs["passwd"],
            "OPTIONS": {"charset": "utf8mb4"}, "ATOMIC_REQUESTS": False,
            "AUTOCOMMIT": True, "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": False,
            "TIME_ZONE": None,
            "TEST": {
                "MIRROR": None,
                "NAME": None,
                "CREATE_DB": False,
                "USER": None,
                "PASSWORD": None,
                "HOST": None,
                "PORT": None,
            },
        }

    def install_django_alias(self, shard=None) -> str:
        if shard is None:
            alias, database_name = self.django_alias, self.database_name
            if self._alias_installed:
                raise RuntimeError("disposable Django alias already installed")
            self._previous_seek_alias = getattr(settings, "SEEK_DATABASE", None)
        else:
            self._assert_owned_shard(shard)
            alias, database_name = shard.django_alias, shard.database_name
            if alias in self._installed_shard_aliases:
                raise RuntimeError(f"disposable shard alias already installed: {alias}")
        if alias in settings.DATABASES or alias in connections.databases:
            raise RuntimeError(f"refusing to replace existing Django alias: {alias}")
        config = {
            **self._django_config(database_name),
        }
        settings.DATABASES[alias] = config
        connections.databases[alias] = config
        settings.SEEK_DATABASE = alias
        if shard is None:
            self._alias_installed = True
        else:
            self._installed_shard_aliases.add(alias)
        return alias

    @classmethod
    def attach_from_identity(cls, identity):
        """Attach as a consumer
        this path can never acquire base-drop authority."""
        return cls._from_identity(identity, owns_base=False)

    @classmethod
    def owner_from_identity(cls, identity):
        """Reconstruct the sole host lane-boundary owner for finalization only."""
        return cls._from_identity(identity, owns_base=True)

    @classmethod
    def _from_identity(cls, identity, *, owns_base):
        kwargs = {
            "host": os.environ["ATTRIBUTE_TEST_DB_HOST"],
            "port": int(os.environ.get("ATTRIBUTE_TEST_DB_PORT", "3306")),
            "user": os.environ["ATTRIBUTE_TEST_DB_USER"],
            "passwd": os.environ["ATTRIBUTE_TEST_DB_PASSWORD"], "charset": "utf8mb4",
        }
        database = cls("attribute_disposable", identity["server_identity"], identity["database_uuid"],
                       identity["database_name"], kwargs, _owns_base=owns_base)
        observed = database.query("SELECT @@server_uuid")[0][0]
        if str(observed) != identity["server_identity"]["server_uuid"]:
            raise RuntimeError("disposable server identity changed")
        return database

    def detach_django_alias(self) -> None:
        if not self._alias_installed:
            return
        connections[self.django_alias].close()
        connections.databases.pop(self.django_alias, None)
        settings.DATABASES.pop(self.django_alias, None)
        if self._previous_seek_alias is None:
            delattr(settings, "SEEK_DATABASE")
        else:
            settings.SEEK_DATABASE = self._previous_seek_alias
        self._alias_installed = False

    def connect(self):
        connection = MySQLdb.connect(db=self.database_name, **self._connection_kwargs)
        telemetry = getattr(self, "_telemetry", None)
        return telemetry.wrap(connection) if telemetry is not None else connection

    def fresh_connection(self):
        return self.connect()

    def rails_database_url(self) -> str:
        """Return the mysql2 URL used only by the disposable Rails container.

        Credentials are percent-encoded and the returned value is passed directly as a
        subprocess environment value
        it is never persisted in evidence or logs.
        """
        user = quote(str(self._connection_kwargs["user"]), safe="")
        password = quote(str(self._connection_kwargs["passwd"]), safe="")
        host = str(self._connection_kwargs["host"])
        port = int(self._connection_kwargs["port"])
        database = quote(self.database_name, safe="")
        return f"mysql2://{user}:{password}@{host}:{port}/{database}?encoding=utf8mb4"

    def execute_sql(self, statements: Iterable[tuple[str, tuple]]) -> None:
        connection = self.connect()
        try:
            cursor = connection.cursor()
            for sql, params in statements:
                cursor.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def seed_seek_fixture(self, fixture: str | dict) -> None:
        """Compile a frozen named or structured SEEK fixture to SQL/params, then execute it.

        Names are restricted to the manifest-owned registry in `seek_fixtures.py`
        structured
        fixtures use the exact `{sample_type_id, sample_titles, samples}` schema. Unknown keys,
        tables, or columns fail before SQL. This method never treats a string as an iterable of SQL.
        """
        from .seek_fixtures import compile_seek_fixture
        if self._template is not None:
            raise RuntimeError("seed is frozen after create_seed_template")
        statements = [(sql, tuple(params)) for sql, params in compile_seek_fixture(fixture)]
        self.execute_sql(statements)
        self._seed_statements.extend(statements)

    def _assert_owned_shard(self, shard):
        if (not isinstance(shard, DatabaseShard)
                or shard.owner_database_uuid != self.database_uuid
                or self._shards.get(shard.shard_id) != shard
                or not shard.database_name.startswith(f"attribute_test_{self.database_uuid.replace('-', '')[:12]}_s_")):
            raise RuntimeError("shard is not owned by this disposable database")

    def _admin_execute(self, sql):
        connection = MySQLdb.connect(db=self._admin_database, **self._connection_kwargs)
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            connection.commit()
        finally:
            connection.close()

    def _replay_seed(self, database_name, statements):
        connection = MySQLdb.connect(db=database_name, **self._connection_kwargs)
        try:
            cursor = connection.cursor()
            for sql, params in statements:
                cursor.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def _semantic_state_sha256(self, database_name):
        projections = (
            "SELECT id,title FROM sample_attribute_types ORDER BY id",
            "SELECT id,title FROM sample_types ORDER BY id",
            "SELECT id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,description,unit_id,sample_controlled_vocab_id,linked_sample_type_id FROM sample_attributes ORDER BY id",
            "SELECT id,sample_type_id,json_metadata FROM samples ORDER BY id",
        )
        connection = MySQLdb.connect(db=database_name, **self._connection_kwargs)
        try:
            rows = []
            cursor = connection.cursor()
            for sql in projections:
                cursor.execute(sql)
                rows.append(list(cursor.fetchall()))
        finally:
            connection.close()
        return hashlib.sha256(orjson.dumps(rows)).hexdigest()

    def create_seed_template(self):
        if self._template is not None:
            return self._template
        if not self._seed_statements:
            raise RuntimeError("cannot freeze an empty logical seed")
        statements = tuple(self._seed_statements)
        logical = hashlib.sha256(orjson.dumps(statements)).hexdigest()
        semantic = self._semantic_state_sha256(self.database_name)
        checksum = hashlib.sha256(orjson.dumps({"logical_seed_sha256": logical, "semantic_state_sha256": semantic}, option=orjson.OPT_SORT_KEYS)).hexdigest()
        self._template = SeedTemplate(checksum, logical, semantic, statements)
        return self._template

    def export_seed_template_descriptor(self, descriptor):
        """Persist a mode-0600 logical descriptor
        callers own seed content."""
        template = self.create_seed_template()
        path = Path(descriptor)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "attribute-seed-template/v1",
            "server_uuid": self.server_identity["server_uuid"],
            "database_uuid": self.database_uuid, "database_name": self.database_name,
            "checksum": template.checksum,
            "logical_seed_sha256": template.logical_seed_sha256,
            "semantic_state_sha256": template.semantic_state_sha256,
            "statements": template.statements,
        }
        descriptor_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor_fd, "wb") as stream:
            stream.write(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return template

    def adopt_precreated_seed_template(self, descriptor, checksum):
        """Adopt verified replay metadata without writing the shared precreated base."""
        if self._owns_base:
            raise RuntimeError("seed adoption is only valid for a non-owning child attachment")
        path = Path(descriptor)
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise RuntimeError("seed descriptor must be an ordinary mode-0600 file")
        payload = orjson.loads(path.read_bytes())
        required = {"schema_version", "server_uuid", "database_uuid", "database_name",
                    "checksum", "logical_seed_sha256", "semantic_state_sha256", "statements"}
        if (set(payload) != required or payload["schema_version"] != "attribute-seed-template/v1"
                or payload["server_uuid"] != self.server_identity["server_uuid"]
                or payload["database_uuid"] != self.database_uuid
                or payload["database_name"] != self.database_name
                or payload["checksum"] != checksum):
            raise RuntimeError("seed descriptor identity/checksum mismatch")
        statements = tuple((str(sql), tuple(params)) for sql, params in payload["statements"])
        logical = hashlib.sha256(orjson.dumps(statements)).hexdigest()
        semantic = self._semantic_state_sha256(self.database_name)
        computed = hashlib.sha256(orjson.dumps(
            {"logical_seed_sha256": logical, "semantic_state_sha256": semantic},
            option=orjson.OPT_SORT_KEYS,
        )).hexdigest()
        if (logical != payload["logical_seed_sha256"]
                or semantic != payload["semantic_state_sha256"] or computed != checksum):
            raise RuntimeError("precreated seed content does not match descriptor")
        self._seed_statements = list(statements)
        self._template = SeedTemplate(checksum, logical, semantic, statements)
        return self._template

    def subprocess_environment(self, shard):
        self._assert_owned_shard(shard)
        return {
            "ATTRIBUTE_TEST_DB_HOST": str(self._connection_kwargs["host"]),
            "ATTRIBUTE_TEST_DB_PORT": str(self._connection_kwargs["port"]),
            "ATTRIBUTE_TEST_DB_USER": str(self._connection_kwargs["user"]),
            "ATTRIBUTE_TEST_DB_PASSWORD": str(self._connection_kwargs["passwd"]),
            "ATTRIBUTE_TEST_DATABASE_NAME": shard.database_name,
            "ATTRIBUTE_TEST_DISPOSABLE_DB_UUID": self.database_uuid,
            "ATTRIBUTE_TEST_DATABASE_PRECREATED": "1",
            "ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID": "1",
        }

    def assert_no_owned_shards(self):
        prefix = f"attribute_test_{self.database_uuid.replace('-', '')[:12]}_s_"
        connection = MySQLdb.connect(db=self._admin_database, **self._connection_kwargs)
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s ORDER BY schema_name", [prefix + "%"])
            remaining = [row[0] for row in cursor.fetchall()]
        finally:
            connection.close()
        if remaining:
            raise AssertionError(f"owned disposable shards remain: {remaining}")

    def clone_shard(self, shard_id):
        if self._template is None:
            raise RuntimeError("create_seed_template must run before clone_shard")
        if (not isinstance(shard_id, str) or not shard_id or len(shard_id) > 64
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in shard_id)):
            raise ValueError("shard_id must be 1-64 safe identifier characters")
        if shard_id in self._shards:
            raise RuntimeError(f"duplicate disposable shard_id: {shard_id}")
        suffix = hashlib.sha256(shard_id.encode()).hexdigest()[:16]
        database_name = f"attribute_test_{self.database_uuid.replace('-', '')[:12]}_s_{suffix}"
        alias = f"attribute_shard_{suffix}"
        if database_name in DENYLIST or len(database_name) > 64:
            raise RuntimeError("derived shard database name is unsafe")
        self._admin_execute(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        self._created_shard_names.add(database_name)
        shard = DatabaseShard(shard_id, database_name, self.database_uuid, self._template.checksum, alias)
        self._shards[shard_id] = shard
        try:
            self._replay_seed(database_name, self._template.statements)
            if self._semantic_state_sha256(database_name) != self._template.semantic_state_sha256:
                raise RuntimeError("cloned shard does not match frozen semantic seed")
        except BaseException:
            self._admin_execute(f"DROP DATABASE IF EXISTS `{database_name}`")
            self._shards.pop(shard_id, None)
            raise
        return shard

    def reset_shard(self, shard, template_checksum):
        self._assert_owned_shard(shard)
        if self._template is None or template_checksum != self._template.checksum or shard.template_checksum != template_checksum:
            raise RuntimeError("shard template checksum mismatch")
        if shard.django_alias in self._installed_shard_aliases:
            connections[shard.django_alias].close()
        self._admin_execute(f"DROP DATABASE `{shard.database_name}`")
        self._admin_execute(f"CREATE DATABASE `{shard.database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        self._replay_seed(shard.database_name, self._template.statements)
        if self._semantic_state_sha256(shard.database_name) != self._template.semantic_state_sha256:
            raise RuntimeError("reset shard does not match frozen semantic seed")
        return shard

    def drop_shard(self, shard):
        self._assert_owned_shard(shard)
        if shard.django_alias in self._installed_shard_aliases:
            connections[shard.django_alias].close()
            connections.databases.pop(shard.django_alias, None)
            settings.DATABASES.pop(shard.django_alias, None)
            self._installed_shard_aliases.remove(shard.django_alias)
            if getattr(settings, "SEEK_DATABASE", None) == shard.django_alias:
                settings.SEEK_DATABASE = self.django_alias
        self._admin_execute(f"DROP DATABASE `{shard.database_name}`")
        self._shards.pop(shard.shard_id)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        connection = self.fresh_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            return list(cursor.fetchall())
        finally:
            connection.close()

    def checksum_query(self, sql: str, params: tuple = ()) -> str:
        return hashlib.sha256(orjson.dumps(self.query(sql, params))).hexdigest()

    def checksum(self, table: str, *, where: dict | None = None) -> str:
        """Hash a frozen table projection in primary-key order
        identifiers are allowlisted."""
        from .seek_fixtures import compile_checksum_query
        sql, params = compile_checksum_query(table, where or {})
        return self.checksum_query(sql, params)

    def teardown(self) -> None:
        for shard in list(self._shards.values()):
            self.drop_shard(shard)
        self.detach_django_alias()
        if not self._owns_base:
            # A spawned benchmark child owns only clones. The host/parent lane
            # boundary remains the sole owner permitted to drop the shared base.
            admin = MySQLdb.connect(db=self._admin_database, **self._connection_kwargs)
            try:
                cursor = admin.cursor()
                for shard_name in sorted(self._created_shard_names):
                    cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", [shard_name])
                    if cursor.fetchone()[0] != 0:
                        raise AssertionError(f"child-owned shard survived teardown: {shard_name}")
            finally:
                admin.close()
            self._torn_down = True
            return
        admin = MySQLdb.connect(db=self._admin_database, **self._connection_kwargs)
        try:
            cursor = admin.cursor()
            cursor.execute(f"DROP DATABASE `{self.database_name}`")
            admin.commit()
            self._torn_down = True
        finally:
            admin.close()

    def assert_torn_down(self) -> None:
        if not self._torn_down:
            raise AssertionError("assert_torn_down is valid only after teardown")
        admin = MySQLdb.connect(db=self._admin_database, **self._connection_kwargs)
        try:
            cursor = admin.cursor()
            cursor.execute("SELECT @@server_uuid")
            assert str(cursor.fetchone()[0]) == self.server_identity["server_uuid"]
            cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", [self.database_name])
            base_count = cursor.fetchone()[0]
            assert base_count == (0 if self._owns_base else 1)
            for shard_name in sorted(self._created_shard_names):
                cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", [shard_name])
                assert cursor.fetchone()[0] == 0
        finally:
            admin.close()


@dataclass(frozen=True)
class WorkerHandle:
    pid: int
    queue: str
    namespace: str


@dataclass
class DisposableAttributeBroker:
    broker_url: str
    namespace: str
    _connection: Connection
    _worker_argv: list[str] | None = None
    _worker_cwd: Path | None = None
    _worker_env: dict[str, str] | None = None
    _worker: subprocess.Popen | None = None
    _event_recorder: subprocess.Popen | None = None
    _event_path: Path | None = None
    _declared_queues: set[str] = field(default_factory=set)
    _published_baseline: dict[str, int] = field(default_factory=dict)
    _torn_down: bool = False

    @classmethod
    def from_environment(cls) -> "DisposableAttributeBroker":
        if os.environ.get("COMPOSE_PROJECT_NAME") == "nextseek":
            raise RuntimeError("refusing the running nextseek compose project")
        namespace = f"attribute-test-{uuid.uuid4()}"
        run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]).resolve()
        default_broker = f"sqla+sqlite:///{(run_root / f'{namespace}.sqlite3').as_posix()}"
        configured = os.environ.get("ATTRIBUTE_TEST_BROKER_URL")
        if configured and configured != default_broker:
            raise RuntimeError(
                "external or unreviewed broker URL; Phase 4 approval required"
            )
        broker_url = default_broker
        connection = Connection(broker_url)
        connection.ensure_connection(max_retries=1)
        return cls(broker_url, namespace, connection)

    def _queue(self, queue: str) -> str:
        if not queue or queue.startswith(f"{self.namespace}:"):
            raise ValueError(
                "queue must be a non-empty logical queue name; namespace is applied exactly once"
            )
        return f"{self.namespace}:{queue}"

    def queue_name(self, queue: str) -> str:
        """Return the only physical queue name that publishers and workers may use."""
        physical = self._queue(queue)
        self._declared_queues.add(physical)
        return physical

    def route_sender(self, sender):
        """Bind a production sender while preserving a logical queue at the call site."""
        def routed(*args, queue: str, **kwargs):
            return sender(*args, queue=self.queue_name(queue), **kwargs)
        return routed

    def start_worker(self, *, queue: str, concurrency: int, environment: dict[str, str] | None = None):
        if self._worker is not None and self._worker.poll() is None:
            raise RuntimeError("worker already running")
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        physical_queue = self.queue_name(queue)
        self._worker_argv = ["python", "-m", "celery", "-A", "nextseek_api.batch_upload.celery_app", "worker", "-Q", physical_queue, "-c", str(concurrency), "--events"]
        self._worker_cwd = Path.cwd()
        if environment is not None and (not isinstance(environment, dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in environment.items())):
            raise ValueError("worker environment must be a string mapping")
        self._worker_env = dict(os.environ)
        self._worker_env.update(environment or {})
        self._worker_env.update(CELERY_BROKER_URL=self.broker_url, CELERY_TASK_DEFAULT_QUEUE=physical_queue,
                                ATTRIBUTE_MUTATION_QUEUE=physical_queue, ATTRIBUTE_TEST_BROKER_NAMESPACE=self.namespace)
        self._event_path = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]) / "celery-events.jsonl"
        if self._event_recorder is None or self._event_recorder.poll() is not None:
            self._event_recorder = subprocess.Popen(
                ["python", "-m", "nextseek_api.attributes.tests.event_recorder", "--broker", self.broker_url,
                 "--queue", physical_queue, "--output", str(self._event_path)], env=self._worker_env,
            )
        self._worker = subprocess.Popen(self._worker_argv, cwd=self._worker_cwd, env=self._worker_env)
        return WorkerHandle(self._worker.pid, physical_queue, self.namespace)

    def kill_worker(self, worker, *, at_fault: str | None = None) -> int:
        if self._worker is None:
            raise RuntimeError("worker not started")
        if at_fault is not None:
            deadline = time.monotonic() + 30
            control = Path(os.environ["ATTRIBUTE_TEST_FAULT_CONTROL"])
            while True:
                durable = json.loads(control.read_text()) if control.is_file() else {"events": []}
                if any(event.get("point") == at_fault for event in durable.get("events", [])):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"worker did not reach fault point: {at_fault}")
                time.sleep(0.05)
        self._worker.kill()
        return self._worker.wait(timeout=30)

    def restart_worker(self, worker):
        if self._worker_argv is None or self._worker_cwd is None:
            raise RuntimeError("worker has no frozen start command")
        if self._worker is not None and self._worker.poll() is None:
            self.kill_worker(worker)
        queue = worker.queue.removeprefix(f"{self.namespace}:")
        return self.start_worker(queue=queue, concurrency=1, environment=self._worker_env)

    def _event_probe(self, event_type: str, message_id: str, queue: str, *, worker=None) -> bool:
        if self._event_path is None or not self._event_path.exists():
            return False
        for line in self._event_path.read_text().splitlines():
            event = orjson.loads(line)
            if event.get("type") == event_type and event.get("uuid") == message_id and event.get("queue") == queue:
                if worker is None or event.get("worker_pid") in (None, worker.pid):
                    return True
        return False

    def published(self, message_id: str, *, queue: str) -> bool:
        return self._event_probe("task-sent", message_id, self.queue_name(queue))

    def consumed(self, message_id: str, *, worker, queue: str) -> bool:
        return self._event_probe("task-succeeded", message_id, self.queue_name(queue), worker=worker)

    def teardown(self) -> None:
        if self._torn_down:
            return
        if self._worker is not None and self._worker.poll() is None:
            self.kill_worker(WorkerHandle(self._worker.pid, self._worker_argv[7], self.namespace))
        if self._event_recorder is not None and self._event_recorder.poll() is None:
            self._event_recorder.terminate()
            self._event_recorder.wait(timeout=30)
        self.assert_torn_down()

    def assert_torn_down(self) -> None:
        if self._torn_down:
            sqlite_path = Path(self.broker_url.removeprefix("sqla+sqlite:///"))
            if sqlite_path.exists():
                raise AssertionError("disposable broker store reappeared after teardown")
            return
        if self._worker is not None and self._worker.poll() is None:
            raise AssertionError("disposable worker is still running")
        if self._event_recorder is not None and self._event_recorder.poll() is None:
            raise AssertionError("event recorder is still running")
        with self._connection.channel() as channel:
            for queue in self._declared_queues:
                _name, messages, consumers = channel.queue_declare(queue=queue, passive=True)
                if messages or consumers:
                    raise AssertionError(f"disposable broker queue not empty: {queue}")
                channel.queue_delete(queue=queue)
        self._connection.close()
        sqlite_path = Path(self.broker_url.removeprefix("sqla+sqlite:///"))
        if not sqlite_path.is_file():
            raise AssertionError("disposable broker store is missing before teardown")
        sqlite_path.unlink()
        if sqlite_path.exists():
            raise AssertionError("disposable broker store survived teardown")
        self._torn_down = True
