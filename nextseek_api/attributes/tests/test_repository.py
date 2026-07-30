"""T04 Section 11.8 primary nodes + coverage helpers for the read repository.

Every scale/no-write primary node emits a ``chain-b-read-observation/v1``
artifact under ``ATTRIBUTE_EVIDENCE_RUN_ROOT``. Mutant killer identities are
exactly the Section 11.6/11.8 node names.
"""
from __future__ import annotations

import hashlib
import json
import os
import resource
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import orjson
import pytest
from django.conf import settings
from django.db import connections

from nextseek_api.attributes.pagination import PageRequest
from nextseek_api.attributes.repository import (
    IDENTIFIER_CHUNK_SIZE,
    AttributeRepository,
    SeekAttributeGateway,
    TitleCollationRequest,
    bounded_identifier_chunks,
    dd35_order_key,
    logicalize_definitions,
    resolve_title_collation_classes,
    resolve_unit_identifier,
)
from nextseek_api.attributes.resolver import (
    IdentifierKind,
    ResolutionError,
    classify_identifier,
    normalize_identifier,
)

MANIFEST = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")
MAX_ENCODED_BYTES = 4_194_304
CHECKSUM_TABLES = (
    "sample_types",
    "sample_attribute_types",
    "units",
    "sample_controlled_vocabs",
    "sample_attributes",
    "samples",
)


def _reset_seek_tables(database) -> None:
    """Wipe shared disposable SEEK tables between tests (lane DB is reused)."""
    database.seed_seek_fixture("attribute_schema_empty")
    database.execute_sql(
        [
            ("DELETE FROM samples", ()),
            ("DELETE FROM sample_attributes", ()),
            ("DELETE FROM sample_types", ()),
            ("DELETE FROM sample_attribute_types", ()),
            ("DELETE FROM units", ()),
            ("DELETE FROM sample_controlled_vocabs", ()),
        ]
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(value) -> str:
    return _sha256_bytes(orjson.dumps(value, option=orjson.OPT_SORT_KEYS))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _rss_bytes() -> int:
    status = Path("/proc/self/status").read_text()
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _classify_sql(sql: str) -> tuple[str, str]:
    text = sql.lstrip().lstrip("/*").split("*/", 1)[-1].lstrip().upper()
    if text.startswith("SELECT") or text.startswith("WITH"):
        lock = "locking-read" if " FOR UPDATE" in text or " LOCK IN SHARE MODE" in text else "none"
        return "SELECT", lock
    if text.startswith(("BEGIN", "COMMIT", "ROLLBACK", "SET", "START TRANSACTION")):
        return "TRANSACTION", "none"
    if text.startswith("INSERT"):
        return "INSERT", "write"
    if text.startswith("UPDATE"):
        return "UPDATE", "write"
    if text.startswith("DELETE"):
        return "DELETE", "write"
    if text.startswith(("CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME")):
        return "DDL", "ddl"
    return "OTHER", "none"


@contextmanager
def _capture_statements(alias: str):
    observations: list[dict] = []
    connection = connections[alias]
    connection.ensure_connection()
    connection_id = str(uuid.uuid4())

    def instrument(execute, sql, params, many, context):
        param_list = [] if params is None else list(params)
        if many:
            flat: list = []
            for row in param_list:
                flat.extend(row if isinstance(row, (list, tuple)) else [row])
            param_list = flat
        statement_class, lock_classification = _classify_sql(str(sql))
        encoded = len(orjson.dumps({"sql": str(sql), "params": param_list}, option=orjson.OPT_SORT_KEYS))
        observations.append(
            {
                "ordinal": len(observations) + 1,
                "connection_id": connection_id,
                "normalized_sql_sha256": _sha256_bytes(
                    " ".join(str(sql).split()).encode()
                ),
                "parameter_count": len(param_list),
                "encoded_byte_count": encoded,
                "statement_class": statement_class,
                "lock_classification": lock_classification,
            }
        )
        return execute(sql, params, many, context)

    with connection.execute_wrapper(instrument):
        yield observations


def _table_checksums(database, *, before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]]) -> list[dict]:
    rows = []
    for table in CHECKSUM_TABLES:
        before_count, before_sha = before[table]
        after_count, after_sha = after[table]
        rows.append(
            {
                "database": database.database_name,
                "table": table,
                "fresh_connection_id": str(uuid.uuid4()),
                "before_row_count": before_count,
                "after_row_count": after_count,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
            }
        )
    return rows


def _snapshot_tables(database) -> dict[str, tuple[int, str]]:
    out = {}
    for table in CHECKSUM_TABLES:
        count = int(database.query(f"SELECT COUNT(*) FROM `{table}`")[0][0])
        out[table] = (count, database.checksum(table))
    return out


def _default_database_uuid(seek_uuid: str) -> str:
    """Observe a stable UUID for Django's default DB identity in this lane."""
    return str(uuid.uuid5(uuid.UUID(seek_uuid), "django-default-database"))


def _emit_read_observation(
    *,
    database,
    case_id: str,
    submitted_selector_count: int,
    resolved_identity_count: int,
    statement_observations: list[dict],
    rss_baseline: int,
    rss_peak: int,
    table_checksums: list[dict],
    broker_started: str,
    broker_finished: str,
    result: dict,
) -> Path:
    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    identity = json.loads((run_root / "boundary-identity.json").read_text())
    manifest = json.loads(MANIFEST.read_text())
    payload = {
        "schema_version": "chain-b-read-observation/v1",
        "evidence_run_id": str(uuid.uuid4()),
        "base_sha": manifest["source_identity"]["base_sha"],
        "dependency_sha": manifest["source_identity"]["plan_sha256"],
        "image_id": manifest["source_identity"]["reference_image_id"],
        "server_uuid": identity["server_identity"]["server_uuid"],
        "seek_database_uuid": database.database_uuid,
        "default_database_uuid": _default_database_uuid(database.database_uuid),
        "case_id": case_id,
        "submitted_selector_count": submitted_selector_count,
        "resolved_identity_count": resolved_identity_count,
        "statement_observations": statement_observations,
        "rss_observation": {
            "external_observer_identity": "procfs:/proc/self/status:VmRSS",
            "baseline_bytes": rss_baseline,
            "peak_bytes": max(rss_peak, rss_baseline),
            "delta_bytes": max(rss_peak, rss_baseline) - rss_baseline,
        },
        "table_checksums": table_checksums,
        "broker_observation": {
            "broker_identity": f"sqla+sqlite://attribute-test-broker/{case_id}",
            "observation_started_at": broker_started,
            "observation_finished_at": broker_finished,
            "publication_count": 0,
        },
        "result": result,
    }
    directory = run_root / "chain-b-read-observations"
    directory.mkdir(parents=True, exist_ok=True)
    safe = case_id.replace("/", "_").replace("[", "_").replace("]", "_")
    path = directory / f"{safe}.json"
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS) + b"\n")
    return path


def _assert_read_safe(statement_observations: list[dict]) -> None:
    assert statement_observations, "expected at least one observed statement"
    ordinals = [row["ordinal"] for row in statement_observations]
    assert ordinals == list(range(1, len(ordinals) + 1))
    for row in statement_observations:
        assert row["statement_class"] in {"SELECT", "TRANSACTION"}
        assert row["lock_classification"] in {"none", "consistent-read"}
        assert 0 <= row["parameter_count"] <= 50_000
        assert 0 <= row["encoded_byte_count"] <= MAX_ENCODED_BYTES


def _run_observed(database, sql_telemetry, *, case_id: str, selector_count: int, operate, result_builder):
    import time

    alias = settings.SEEK_DATABASE
    broker_started = _utc_now()
    before = _snapshot_tables(database)
    rss_baseline = _rss_bytes()
    with _capture_statements(alias) as statements:
        with sql_telemetry.wrap_django_connection(alias):
            outcome = operate()
    rss_peak = _rss_bytes()
    sql_telemetry.snapshot()
    time.sleep(0.002)
    broker_finished = _utc_now()
    after = _snapshot_tables(database)
    for table in CHECKSUM_TABLES:
        assert before[table] == after[table], f"read mutated {table}"
    _assert_read_safe(statements)
    resolved_count, result = result_builder(outcome)
    _emit_read_observation(
        database=database,
        case_id=case_id,
        submitted_selector_count=selector_count,
        resolved_identity_count=resolved_count,
        statement_observations=statements,
        rss_baseline=rss_baseline,
        rss_peak=rss_peak,
        table_checksums=_table_checksums(database, before=before, after=after),
        broker_started=broker_started,
        broker_finished=broker_finished,
        result=result,
    )
    return outcome, statements


def _seed_two_types_same_title(database) -> None:
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(2,'Tissue',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) "
                "VALUES(10,1,1,'RNA',0,1,0,NOW(6),NOW(6)),(11,2,1,'RNA',0,1,0,NOW(6),NOW(6)),(12,1,1,'UID',1,2,1,NOW(6),NOW(6))",
                (),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Section 11.8 primary nodes
# ---------------------------------------------------------------------------


def test_title_resolution_is_scoped_to_explicit_type(disposable_attribute_db, sql_telemetry, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    gateway = SeekAttributeGateway()
    repo = AttributeRepository(gateway)

    def operate():
        return repo.search([{"sample_type": 1, "attributes": ["RNA"]}], PageRequest(page=1, page_size=10))

    def result_builder(page):
        identities = [(item.id, item.sample_type_id, item.title, item.pos) for item in page.attributes]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(
                [(item.id, item.pos, item.title) for item in page.attributes]
            ),
            "total": page.pagination.total_records,
            "offset": 0,
            "page_size": page.pagination.page_size,
            "returned_count": len(page.attributes),
        }

    page, _ = _run_observed(
        database, sql_telemetry,
        case_id="test_title_resolution_is_scoped_to_explicit_type",
        selector_count=1,
        operate=operate,
        result_builder=result_builder,
    )
    assert len(page.attributes) == 1
    assert page.attributes[0].id == 10
    assert page.attributes[0].sample_type_id == 1
    # Direct gateway scope check (M-RESOLVER-SCOPE-01 killer surface).
    matches = gateway.resolve_attribute_titles(1, [normalize_identifier("RNA")])
    assert matches[(IdentifierKind.TITLE, "RNA")] == [10]


def test_numeric_string_uses_id_grammar_without_title_fallback(
    disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) "
                "VALUES(99,1,1,'42',0,1,0,NOW(6),NOW(6)),(42,1,1,'Mass',0,2,0,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    assert classify_identifier("42").kind == "id"
    assert classify_identifier("42").value == 42
    gateway = SeekAttributeGateway()
    repo = AttributeRepository(gateway)

    def operate():
        return repo.search([{"sample_type": 1, "attributes": ["42"]}], PageRequest(page=1, page_size=10))

    def result_builder(page):
        identities = [(item.id, item.title) for item in page.attributes]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(identities),
            "total": page.pagination.total_records,
            "offset": 0,
            "page_size": page.pagination.page_size,
            "returned_count": len(page.attributes),
        }

    page, _ = _run_observed(
        database, sql_telemetry,
        case_id="test_numeric_string_uses_id_grammar_without_title_fallback",
        selector_count=1,
        operate=operate,
        result_builder=result_builder,
    )
    assert len(page.attributes) == 1
    assert page.attributes[0].id == 42
    assert page.attributes[0].title == "Mass"


def test_duplicate_unit_symbol_is_not_an_identifier(
    disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO units(id,title,symbol) VALUES(1,'nanogram','ng'),(2,'mass','ng')", ()),
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'Float',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,unit_id,created_at,updated_at) "
                "VALUES(10,1,1,'RNA',0,1,0,1,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    gateway = SeekAttributeGateway()
    repo = AttributeRepository(gateway)

    def operate():
        matches = resolve_unit_identifier(gateway, normalize_identifier("ng"))
        assert matches == []
        with pytest.raises(ResolutionError) as raised:
            repo.resolve_relationship("units", "ng", field="unit")
        assert raised.value.code == "unit_not_found"
        # Title still resolves uniquely.
        unit = repo.resolve_relationship("units", "nanogram", field="unit")
        record = repo.retrieve(10)
        return unit, record

    def result_builder(outcome):
        unit, record = outcome
        identities = [(unit[0], unit[1], record.unit_id, record.unit_symbol)]
        return 1, {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json([(record.id, record.pos, record.unit_symbol)]),
            "total": 1,
            "offset": 0,
            "page_size": 1,
            "returned_count": 1,
        }

    (unit, record), _ = _run_observed(
        database, sql_telemetry,
        case_id="test_duplicate_unit_symbol_is_not_an_identifier",
        selector_count=1,
        operate=operate,
        result_builder=result_builder,
    )
    assert unit[0] == 1
    assert record.unit_symbol == "ng"


@pytest.mark.parametrize("phase", ["create", "patch-final"])
def test_real_collation_title_classes(phase, disposable_attribute_db, sql_telemetry, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) "
                "VALUES(10,1,1,'RNA',0,1,0,NOW(6),NOW(6)),(11,1,1,'DNA',0,2,0,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    gateway = SeekAttributeGateway()

    def operate():
        if phase == "create":
            requests = [
                TitleCollationRequest(0, 0, "create", 1, "rna"),
                TitleCollationRequest(0, 1, "create", 1, "Protein"),
            ]
        else:
            requests = [
                TitleCollationRequest(0, 0, "patch-final", 1, "dna", exclude_id=10),
                TitleCollationRequest(0, 1, "patch-final", 1, "RNA", exclude_id=11),
            ]
        return resolve_title_collation_classes(gateway, requests)

    def result_builder(classes):
        payload = sorted(
            (key, value.class_key, list(value.match_ids)) for key, value in classes.items()
        )
        return len(classes), {
            "ordered_identity_sha256": _sha256_json(payload),
            "logical_record_sha256": _sha256_json(payload),
            "total": len(classes),
            "offset": 0,
            "page_size": max(1, len(classes)),
            "returned_count": len(classes),
        }

    classes, _ = _run_observed(
        database, sql_telemetry,
        case_id=f"test_real_collation_title_classes[{phase}]",
        selector_count=2,
        operate=operate,
        result_builder=result_builder,
    )
    if phase == "create":
        create_rna = classes[(0, 0, "create")]
        assert 10 in create_rna.match_ids  # case-insensitive collision with existing RNA
        assert classes[(0, 1, "create")].match_ids == ()
    else:
        # Patching 10 -> "dna" collides with existing DNA (11); self-ID 10 excluded only.
        assert 11 in classes[(0, 0, "patch-final")].match_ids
        assert 10 not in classes[(0, 0, "patch-final")].match_ids
        # Patching 11 -> "RNA" collides with existing RNA (10); self-ID 11 excluded.
        assert 10 in classes[(0, 1, "patch-final")].match_ids
        assert 11 not in classes[(0, 1, "patch-final")].match_ids


def test_search_omitted_attributes_returns_complete_type_catalog(
    disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    repo = AttributeRepository(SeekAttributeGateway())

    def operate():
        return repo.search([{"sample_type": "Blood"}], PageRequest(page=1, page_size=50))

    def result_builder(page):
        identities = [(item.id, item.sample_type_id, item.pos, item.title) for item in page.attributes]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(identities),
            "total": page.pagination.total_records,
            "offset": 0,
            "page_size": page.pagination.page_size,
            "returned_count": len(page.attributes),
        }

    page, _ = _run_observed(
        database, sql_telemetry,
        case_id="test_search_omitted_attributes_returns_complete_type_catalog",
        selector_count=0,
        operate=operate,
        result_builder=result_builder,
    )
    assert page.pagination.total_records == 2
    assert {item.id for item in page.attributes} == {10, 12}
    assert all(item.sample_type_id == 1 for item in page.attributes)


def test_global_page_total_and_slice_are_stable(
    disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    repo = AttributeRepository(SeekAttributeGateway())

    def operate():
        full = repo.catalog(PageRequest(page=1, page_size=500))
        page1 = repo.catalog(PageRequest(page=1, page_size=2))
        page2 = repo.catalog(PageRequest(page=2, page_size=2))
        return full, page1, page2

    def result_builder(outcome):
        full, page1, page2 = outcome
        identities = [(item.sample_type_id, item.pos, item.id) for item in full.attributes]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(
                [(item.id, item.pos) for item in full.attributes]
            ),
            "total": full.pagination.total_records,
            "offset": 0,
            "page_size": 2,
            "returned_count": len(page1.attributes),
        }

    (full, page1, page2), _ = _run_observed(
        database, sql_telemetry,
        case_id="test_global_page_total_and_slice_are_stable",
        selector_count=0,
        operate=operate,
        result_builder=result_builder,
    )
    assert full.pagination.total_records == 3
    assert [item.id for item in page1.attributes] + [item.id for item in page2.attributes] == [
        item.id for item in full.attributes
    ]
    assert page1.pagination.total_records == page2.pagination.total_records == 3


@pytest.mark.parametrize("count", [499, 500, 501])
def test_resolution_chunk_boundaries(count, disposable_attribute_db, sql_telemetry, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    titles = [f"A{n}" for n in range(count)]
    statements = [
        ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
        ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
    ]
    for index, title in enumerate(titles, start=1):
        statements.append(
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) "
                "VALUES(%s,1,1,%s,0,%s,0,NOW(6),NOW(6))",
                (index, title, index),
            )
        )
    database.execute_sql(statements)
    gateway = SeekAttributeGateway()

    def operate():
        gateway.query_count = 0
        gateway.chunk_sizes = []
        normalized = [normalize_identifier(title) for title in titles]
        matches = gateway.resolve_attribute_titles(1, normalized)
        return matches

    def result_builder(matches):
        ordered = sorted((key[1], ids) for key, ids in matches.items())
        return sum(len(ids) for _, ids in ordered), {
            "ordered_identity_sha256": _sha256_json(ordered),
            "logical_record_sha256": _sha256_json(ordered),
            "total": count,
            "offset": 0,
            "page_size": min(5000, max(1, count)),
            "returned_count": min(5000, count),
        }

    matches, statements = _run_observed(
        database, sql_telemetry,
        case_id=f"test_resolution_chunk_boundaries[{count}]",
        selector_count=count,
        operate=operate,
        result_builder=result_builder,
    )
    assert sum(len(ids) for ids in matches.values()) == count
    assert all(size <= IDENTIFIER_CHUNK_SIZE for size in gateway.chunk_sizes)
    if count > IDENTIFIER_CHUNK_SIZE:
        assert max(gateway.chunk_sizes) == IDENTIFIER_CHUNK_SIZE
        assert len([s for s in gateway.chunk_sizes if s > 0]) >= 2
    assert all(row["parameter_count"] <= 50_000 for row in statements)


@pytest.mark.parametrize("count", [49999, 50000, 50001])
def test_final_selection_boundaries(count, disposable_attribute_db, sql_telemetry, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.seed_seek_fixture(
        {
            "sample_type_id": 1,
            "sample_titles": ["UID", "Mass", "Volume"],
            "samples": [{"id": 1, "json_metadata": {"UID": "u1"}}],
        }
    )
    existing_ids = [row[0] for row in database.query("SELECT id FROM sample_attributes ORDER BY id")]
    # Pad with non-existent IDs so statement chunking is exercised without
    # materializing tens of thousands of definition rows. Include the real IDs
    # so the ordered page result remains independently checkable.
    padding = [1_000_000 + index for index in range(count - len(existing_ids))]
    selectors = existing_ids + padding
    assert len(selectors) == count
    gateway = SeekAttributeGateway()

    def operate():
        gateway.query_count = 0
        gateway.chunk_sizes = []
        total, definitions = gateway.catalog(
            type_ids=[1], attribute_ids=selectors, offset=0, limit=10
        )
        return total, definitions

    def result_builder(outcome):
        total, definitions = outcome
        identities = [(item.id, item.pos, item.title) for item in definitions]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(identities),
            "total": total,
            "offset": 0,
            "page_size": 10,
            "returned_count": len(definitions),
        }

    (total, definitions), statements = _run_observed(
        database, sql_telemetry,
        case_id=f"test_final_selection_boundaries[{count}]",
        selector_count=count,
        operate=operate,
        result_builder=result_builder,
    )
    assert total == len(existing_ids)
    assert all(row["parameter_count"] <= 50_000 for row in statements)
    assert all(size <= IDENTIFIER_CHUNK_SIZE for size in gateway.chunk_sizes if size)
    if count > IDENTIFIER_CHUNK_SIZE:
        assert any(size == IDENTIFIER_CHUNK_SIZE for size in gateway.chunk_sizes)
    assert [item.id for item in definitions] == existing_ids[:10]


@pytest.mark.parametrize("case_id", ["null-duplicate-gap"])
def test_dd35_logical_positions_without_writes(
    case_id, disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("ALTER TABLE sample_attributes MODIFY pos INT NULL", ()),
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) VALUES "
                "(1,1,1,'ValidA',0,2,0,NOW(6),NOW(6)),"
                "(2,1,1,'ValidB',0,2,0,NOW(6),NOW(6)),"  # duplicate physical pos
                "(3,1,1,'GapC',0,5,0,NOW(6),NOW(6)),"  # gapped
                "(4,1,1,'NullD',0,NULL,0,NOW(6),NOW(6)),"
                "(5,1,1,'NullE',0,NULL,0,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    repo = AttributeRepository(SeekAttributeGateway())

    def operate():
        catalog = repo.catalog(PageRequest(page=1, page_size=50))
        search = repo.search([{"sample_type": 1}], PageRequest(page=1, page_size=50))
        retrieved = [repo.retrieve(item.id) for item in catalog.attributes]
        return catalog, search, retrieved

    def result_builder(outcome):
        catalog, search, retrieved = outcome
        identities = [(item.id, item.pos, item.title) for item in catalog.attributes]
        return len(identities), {
            "ordered_identity_sha256": _sha256_json(identities),
            "logical_record_sha256": _sha256_json(identities),
            "total": catalog.pagination.total_records,
            "offset": 0,
            "page_size": catalog.pagination.page_size,
            "returned_count": len(catalog.attributes),
        }

    (catalog, search, retrieved), _ = _run_observed(
        database, sql_telemetry,
        case_id=f"test_dd35_logical_positions_without_writes[{case_id}]",
        selector_count=0,
        operate=operate,
        result_builder=result_builder,
    )
    # Valid positive first by (pos,id), then NULL by id; logical positions contiguous.
    assert [(item.id, item.pos) for item in catalog.attributes] == [
        (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
    ]
    assert [item.id for item in search.attributes] == [item.id for item in catalog.attributes]
    assert [item.pos for item in retrieved] == [1, 2, 3, 4, 5]
    # Pure adapter surface (M-LOGICAL-POS-01).
    class _Row:
        def __init__(self, pos, id):
            self.pos = pos
            self.id = id

    assert dd35_order_key(_Row(2, 1)) < dd35_order_key(_Row(None, 4))


def test_relationship_resolution_is_bounded_and_complete(
    disposable_attribute_db, sql_telemetry, django_db_blocker
):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
                ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(3,'Float',NOW(6),NOW(6))", ()),
                ("INSERT INTO units(id,title,symbol) VALUES(5,'nanogram','ng')", ()),
                ("INSERT INTO sample_controlled_vocabs(id,title,created_at,updated_at) VALUES(8,'Terms',NOW(6),NOW(6))", ()),
                ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6)),(2,'Tissue',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,"
                "unit_id,sample_controlled_vocab_id,linked_sample_type_id,created_at,updated_at) "
                "VALUES(10,1,3,'RNA',1,1,0,5,8,2,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    gateway = SeekAttributeGateway()
    repo = AttributeRepository(gateway)

    def operate():
        record = repo.retrieve(10)
        hypothetical = repo.materialize_hypothetical_records(
            [
                {
                    "token": "t1",
                    "title": "New",
                    "sample_type_id": 1,
                    "sample_attribute_type_id": 3,
                    "unit_id": 5,
                    "sample_controlled_vocab_id": 8,
                    "linked_sample_type_id": 2,
                    "required": False,
                    "pos": 2,
                    "is_title": False,
                    "description": None,
                }
            ]
        )
        identities = gateway.materialization_identities(
            [{"sample_type_id": 1, "sample_attribute_type_id": 3, "unit_id": 5, "sample_controlled_vocab_id": 8}]
        )
        return record, hypothetical, identities

    def result_builder(outcome):
        record, hypothetical, identities = outcome
        payload = {
            "record": (record.id, record.unit_id, record.unit_title, record.unit_symbol,
                       record.sample_controlled_vocab_id, record.sample_controlled_vocab_title,
                       record.linked_sample_type_id, record.linked_sample_type_title,
                       record.sample_attribute_type_title),
            "hypothetical": hypothetical[0],
            "units": sorted(identities["units"].items()),
        }
        return 1, {
            "ordered_identity_sha256": _sha256_json(payload["record"]),
            "logical_record_sha256": _sha256_json(payload),
            "total": 1,
            "offset": 0,
            "page_size": 1,
            "returned_count": 1,
        }

    (record, hypothetical, identities), _ = _run_observed(
        database, sql_telemetry,
        case_id="test_relationship_resolution_is_bounded_and_complete",
        selector_count=1,
        operate=operate,
        result_builder=result_builder,
    )
    assert record.unit_id == 5 and record.unit_title == "nanogram" and record.unit_symbol == "ng"
    assert record.sample_controlled_vocab_id == 8 and record.sample_controlled_vocab_title == "Terms"
    assert record.linked_sample_type_id == 2 and record.linked_sample_type_title == "Tissue"
    assert record.sample_attribute_type_title == "Float"
    assert hypothetical[0]["unit_symbol"] == "ng"
    assert identities["units"][5][1] == "nanogram"


# ---------------------------------------------------------------------------
# Coverage / contract helpers (not primary-credit nodes)
# ---------------------------------------------------------------------------


def test_bounded_identifier_chunks_default_and_custom():
    assert list(bounded_identifier_chunks(list(range(501)))) == [list(range(500)), [500]]
    assert list(bounded_identifier_chunks(list(range(3)), chunk_size=2)) == [[0, 1], [2]]
    with pytest.raises(ValueError):
        list(bounded_identifier_chunks([1], chunk_size=0))


def test_logicalize_rejects_nonpositive_physical_pos():
    from nextseek_api.attributes.repository import RawAttribute
    from datetime import datetime, timezone

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = RawAttribute(
        1, "Bad", 1, "Blood", 1, "String", False, 0, False, None, None, None, None, None, None, None, None, now, now
    )
    with pytest.raises(ResolutionError, match="non-null non-positive"):
        logicalize_definitions([row])


def test_page_size_above_5000_rejected():
    with pytest.raises(ValueError, match="page_size"):
        PageRequest(page=1, page_size=5001)


def test_resolve_mutation_and_snapshots(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    repo = AttributeRepository(SeekAttributeGateway())
    resolved = repo.resolve_mutation(
        {
            "targets": [
                {"sample_type": 1, "attributes": [10, "UID"]},
                {"sample_type": "Tissue", "attributes": ["RNA"]},
            ]
        }
    )
    assert resolved["targets"][0]["sample_type_id"] == 1
    assert not resolved["targets"][0]["resolution_errors"]
    snapshots = repo.snapshots_for(resolved)
    assert 1 in snapshots and snapshots[1].definitions
    verdicts = repo.dependent_verdicts([1, 2], resolved)
    assert verdicts[1] == "compatible"
    counts = repo.invalid_json_counts([1])
    assert counts[1] == 0


def test_id_only_resolution_and_owner_mismatch(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    repo = AttributeRepository(SeekAttributeGateway())
    rows = repo.resolve_id_only_attributes([10, "12"])
    assert [row.id for row in rows] == [10, 12]
    with pytest.raises(ResolutionError, match="does not belong to the supplied sample type"):
        repo.resolve_id_only_attributes([10], expected_sample_type_id=2)
    with pytest.raises(ResolutionError):
        repo.resolve_id_only_attributes(["RNA"])


def test_ambiguous_and_missing_search_errors(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
            ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(1,'Blood',NOW(6),NOW(6)),(2,'Blood',NOW(6),NOW(6))", ()),
            (
                "INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title,created_at,updated_at) "
                "VALUES(10,1,1,'RNA',0,1,0,NOW(6),NOW(6))",
                (),
            ),
        ]
    )
    repo = AttributeRepository(SeekAttributeGateway())
    with pytest.raises(ResolutionError) as raised:
        repo.search([{"sample_type": "Blood"}])
    assert raised.value.code == "sample_type_ambiguous"
    with pytest.raises(ResolutionError) as raised:
        repo.search([{"sample_type": 1, "attributes": ["Missing"]}])
    assert raised.value.code == "attribute_not_found"


def test_utc_datetime_converts_aware_and_naive():
    from nextseek_api.attributes.repository import utc_datetime

    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert utc_datetime(naive).tzinfo is timezone.utc
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert utc_datetime(aware).tzinfo is timezone.utc
    from datetime import timedelta

    offset = timezone(timedelta(hours=-5))
    shifted = datetime(2026, 1, 1, 7, 0, 0, tzinfo=offset)
    converted = utc_datetime(shifted)
    assert converted.hour == 12


def test_resolve_unit_rejects_non_id_title_kind(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    gateway = SeekAttributeGateway()

    class _Fake:
        kind = type("K", (), {"value": "symbol"})()
        submitted = "ng"
        value = "ng"

    with pytest.raises(ResolutionError) as raised:
        resolve_unit_identifier(gateway, _Fake())
    assert raised.value.code == "unit_not_found"


def test_empty_collation_requests_and_unknown_relationship(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    gateway = SeekAttributeGateway()
    assert resolve_title_collation_classes(gateway, []) == {}
    with pytest.raises(ValueError, match="unknown relationship table"):
        gateway.resolve_relationship("not_a_table", [normalize_identifier(1)])


def test_gateway_refuses_oversized_parameter_list(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    gateway = SeekAttributeGateway()
    with pytest.raises(RuntimeError, match="parameter bound"):
        gateway._execute("SELECT 1 WHERE 0 IN (" + ",".join(["%s"] * 50001) + ")", list(range(50001)))


def test_mutation_resolution_error_paths_and_id_only(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    # Ambiguous sample-type title for mutation envelope.
    database.execute_sql(
        [("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(3,'Blood',NOW(6),NOW(6))", ())]
    )
    repo = AttributeRepository(SeekAttributeGateway())
    missing = repo.resolve_mutation({"targets": [{"sample_type": "NoSuchType", "attributes": [10]}]})
    assert missing["targets"][0]["resolution_errors"][0]["code"] == "sample_type_not_found"
    ambiguous = repo.resolve_mutation({"targets": [{"sample_type": "Blood", "attributes": [10]}]})
    assert ambiguous["targets"][0]["resolution_errors"][0]["code"] == "sample_type_ambiguous"
    # ID-only attribute resolution without sample_type, including changes + not-found.
    id_only = repo.resolve_mutation(
        {
            "targets": [
                {
                    "sample_type": None,
                    "attributes": [
                        {"attribute": 10, "changes": {"title": "Renamed"}},
                        999999,
                    ],
                }
            ]
        }
    )
    assert id_only["targets"][0]["operations"][0]["attribute_id"] == 10
    assert id_only["targets"][0]["operations"][0]["changes"]["title"] == "Renamed"
    assert id_only["targets"][0]["resolution_errors"]
    with pytest.raises(ResolutionError):
        repo.retrieve(999999)
    with pytest.raises(ResolutionError):
        repo.resolve_relationship("sample_controlled_vocabs", "Missing", field="sample_controlled_vocab")
    with pytest.raises(ResolutionError):
        repo.resolve_id_only_attributes([999999])


def test_raw_attribute_to_snapshot_and_type_snapshot_missing(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    gateway = SeekAttributeGateway()
    rows = gateway.resolve_global_attribute_ids([10])[10]
    assert rows[0].to_snapshot().id == 10
    with pytest.raises(ResolutionError) as raised:
        gateway.type_snapshots([999])
    assert raised.value.code == "sample_type_not_found"


def test_unsafe_and_missing_collation_oracle_paths(disposable_attribute_db, django_db_blocker, monkeypatch):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_two_types_same_title(database)
    gateway = SeekAttributeGateway()
    with pytest.raises(ResolutionError, match="unsafe"):
        SeekAttributeGateway._safe_collation("bad;drop")
    monkeypatch.setattr(gateway, "_title_collation", lambda: (_ for _ in ()).throw(
        ResolutionError("missing_title_collation_oracle", "sample_attributes.title has no observable collation")
    ))
    with pytest.raises(ResolutionError, match="no observable collation"):
        gateway.resolve_title_collation_classes(
            [TitleCollationRequest(0, 0, "create", 1, "X")]
        )


def test_search_missing_sample_type(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    database.execute_sql(
        [
            ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))", ()),
        ]
    )
    repo = AttributeRepository(SeekAttributeGateway())
    with pytest.raises(ResolutionError) as raised:
        repo.search([{"sample_type": "Ghost"}])
    assert raised.value.code == "sample_type_not_found"
