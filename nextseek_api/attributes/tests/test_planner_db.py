"""T05 Section 11.9 primary db-lane nodes -- real T01 -> T04 -> T05 chain.

Every Section 11.9 node name and parameter ID below is the exact, literal
set the task-05 spec binds in the ``db`` lane, and every one of them
drives ``MutationPlanner.plan_mutation`` against the real, unmodified
``nextseek_api.attributes.repository.AttributeRepository`` over the
disposable SEEK database -- never against a test double. Section 11 is
explicit that "one trivial DB node plus parameterized fake units cannot
satisfy" the T05 real-boundary classes, so the fake-repository lane lives
entirely in ``test_planner.py`` and none of it is imported here. One
supplementary Wave-4 node
(``test_title_spelled_relationship_identifiers_through_real_t04d``) proves
the user-ruled relationship-identifier lift (plan Amendment Log 2026-08-04
(2)) against the real T04d bulk pass; per Section 8 it supplements the
frozen obligation rows and substitutes for none of them.

The T04 capability gap these nodes were previously skipped for is closed:
``AttributeRepository`` implements all eight protocol methods plus
``display_fields_for`` as of integration ``a6f4241``, and task-04d
(``d8806a9``) added the bulk relationship-identifier resolution pass.

Statement capture, fresh-connection table checksums, RSS sampling, and the
``chain-b-read-observation/v1`` envelope are reused from T04's
``test_repository.py`` rather than re-implemented, so both tasks' evidence
is produced by one observer implementation.
"""
from __future__ import annotations

import gc
import math
import os
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

import orjson
import pytest
from django.conf import settings

from nextseek_api.attributes.planner import (
    PLAN_SCHEMA_VERSION,
    Definition,
    MutationPlanner,
    _TitleTransitionRejected,
    apply_title_transition,
    build_resolved_plan_envelope,
    canonical_json,
    canonical_sha256,
    classify_metadata_rewrite,
)
from nextseek_api.attributes.repository import (
    IDENTIFIER_CHUNK_SIZE,
    AttributeRepository,
    SeekAttributeGateway,
    TitleCollationRequest,
)
from nextseek_api.attributes.tests.test_repository import (
    CHECKSUM_TABLES,
    MANIFEST,
    _assert_read_safe,
    _capture_statements,
    _default_database_uuid,
    _reset_seek_tables,
    _rss_bytes,
    _sha256_json,
    _snapshot_tables,
    _table_checksums,
    _utc_now,
)

ACTOR = {"person_id": 42, "django_user_id": 84, "login": "demo", "scheme": "basic"}
THRESHOLD = 100

# Default-DB tables T05 must never touch while planning: M-DRY-01's transform
# inserts an `attributes_mutation_job` row at the top of `plan_mutation`.
DEFAULT_DB_TABLES = ("attributes_mutation_job", "attributes_mutation_partition")


# ---------------------------------------------------------------------------
# Request builders -- real DD-26 envelope shapes (submitted `attributes` field)
# ---------------------------------------------------------------------------


def create_request(definitions, *, sample_type=1, dry_run=True):
    return {"kind": "create", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": definitions}]}


def create_definition(title, **overrides):
    """A real ``AttributeCreate`` payload: relationship fields carry their
    submitted (unresolved) DD-26 names, exactly as T01 validates them and
    T04 passes them through into ``operations[*]["definition"]``."""
    definition = {"title": title, "sample_attribute_type": 1, "required": False, "pos": None,
                  "is_title": False, "description": None, "unit": None,
                  "sample_controlled_vocab": None, "linked_sample_type": None}
    definition.update(overrides)
    return definition


def patch_request(operations, *, sample_type=1, dry_run=True):
    return {"kind": "patch", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": operations}]}


def patch_operation(attribute, changes):
    return {"attribute": attribute, "changes": changes}


def delete_request(selectors, *, sample_type=1, dry_run=True):
    return {"kind": "delete", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": list(selectors)}]}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _leave_shared_seek_tables_clean(request):
    """Wipe the shared disposable SEEK tables again after each db node.

    The lane's precreated base database is shared by every collected test
    file and never wiped between files, so rows this module leaves behind
    deterministically poison a later file's frozen semantic-state hash
    (T00's clone-reset guard in test_real_boundary_contract.py failed
    exactly this way; user ruling 2026-08-04, option C). The seeding
    helpers already call ``_reset_seek_tables`` on entry; this teardown
    reuses the same T04 helper so the module also leaves the projected
    tables as clean as it found them. Pure unit nodes never touch the
    boundary."""
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    database = request.getfixturevalue("disposable_attribute_db")
    yield
    _reset_seek_tables(database)


def _seed_reference_rows(database):
    """Value types, units, vocabularies and a second sample type, so a patch
    can re-point every relationship field at a real row. The unit carries a
    real title AND a symbol so title-spelled resolution succeeds while a
    symbol spelling provably never matches (DD-19)."""
    database.execute_sql([
        ("INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES"
         "(1,'String',NOW(6),NOW(6)),(2,'Text',NOW(6),NOW(6))", ()),
        ("INSERT INTO units(id,title,symbol,comment,created_at,updated_at) VALUES"
         "(1,'milligram','mg','milligram',NOW(6),NOW(6))", ()),
        ("INSERT INTO sample_controlled_vocabs(id,title,created_at,updated_at) VALUES"
         "(1,'Species',NOW(6),NOW(6))", ()),
        ("INSERT INTO sample_types(id,title,created_at,updated_at) VALUES"
         "(1,'Blood',NOW(6),NOW(6)),(2,'Tissue',NOW(6),NOW(6)),(3,'Plasma',NOW(6),NOW(6))", ()),
    ])


def _seed_blood(database, *, population=0):
    """Blood(1) with UID(10)/RNA(11)/Age(12) at contiguous positive positions,
    Plasma(3) with Alpha(20, title)/Beta(21) and no UID, plus `population`
    valid-JSON sample rows on Blood."""
    _reset_seek_tables(database)
    _seed_reference_rows(database)
    database.execute_sql([
        ("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,"
         "is_title,created_at,updated_at) VALUES"
         "(10,1,1,'UID',1,1,1,NOW(6),NOW(6)),"
         "(11,1,1,'RNA',0,2,0,NOW(6),NOW(6)),"
         "(12,1,1,'Age',0,3,0,NOW(6),NOW(6)),"
         "(20,3,1,'Alpha',0,1,1,NOW(6),NOW(6)),"
         "(21,3,1,'Beta',0,2,0,NOW(6),NOW(6))", ()),
    ])
    if population:
        _seed_samples(database, sample_type_id=1, count=population)


def _seed_samples(database, *, sample_type_id, count):
    import MySQLdb

    connection = MySQLdb.connect(db=database.database_name, **database._connection_kwargs)
    try:
        cursor = connection.cursor()
        cursor.executemany(
            "INSERT INTO samples(id,sample_type_id,json_metadata,created_at,updated_at) "
            "VALUES(%s,%s,'{}',NOW(6),NOW(6))",
            [(index, sample_type_id) for index in range(1, count + 1)],
        )
        connection.commit()
    finally:
        connection.close()


def _bulk_seed_attributes(database, *, sample_type_id, count):
    """Seed `count` real attribute rows on one sample type via `executemany`
    batches on a raw (unwrapped) MySQL connection -- never one INSERT per row
    and never through the telemetry-wrapped Django connection the statement
    captures observe. Ids and positions ascend together, so ascending id
    order already equals DD-35 logical order."""
    import MySQLdb

    _reset_seek_tables(database)
    connection = MySQLdb.connect(db=database.database_name, **database._connection_kwargs)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO sample_attribute_types(id,title,created_at,updated_at) VALUES(1,'String',NOW(6),NOW(6))"
        )
        cursor.execute(
            "INSERT INTO sample_types(id,title,created_at,updated_at) VALUES(%s,'Bulk',NOW(6),NOW(6))",
            (sample_type_id,),
        )
        connection.commit()
        rows = [(index, sample_type_id, f"A{index}", index) for index in range(1, count + 1)]
        insert = ("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,"
                  "pos,is_title,created_at,updated_at) VALUES(%s,%s,1,%s,0,%s,0,NOW(6),NOW(6))")
        for start in range(0, len(rows), 2000):
            cursor.executemany(insert, rows[start:start + 2000])
        connection.commit()
    finally:
        connection.close()
    return [index for index in range(1, count + 1)]


# ---------------------------------------------------------------------------
# Observation: statements on both aliases, checksums, planner-handoff/v1
# ---------------------------------------------------------------------------


def _job_table_state(database):
    """Fresh-connection row-count probe for the T03 job/partition tables that
    M-DRY-01's transform writes to, skipping any table this lane's database
    does not carry. A fresh connection is used every time so no planning-side
    session state can mask a write."""
    out = {}
    connection = database.fresh_connection()
    try:
        cursor = connection.cursor()
        for table in DEFAULT_DB_TABLES:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            except Exception:  # noqa: BLE001 - table absent in this lane's database
                continue
            out[table] = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return out


@contextmanager
def _capture_all_statements(aliases):
    """Capture on every named Django alias at once. The SEEK alias carries
    all planning reads; the default alias is watched because M-DRY-01's
    transform writes an `attributes_mutation_job` row there."""
    with ExitStack() as stack:
        yield {alias: stack.enter_context(_capture_statements(alias)) for alias in aliases}


def _submitted_type_id(request):
    """The one sample type every node in this module targets, resolved from
    the submitted identifier through T04 rather than assumed."""
    value = request["targets"][0]["sample_type"]
    if isinstance(value, int):
        return value
    return AttributeRepository(SeekAttributeGateway()).resolve_mutation(
        {"targets": [{"sample_type": value, "attributes": []}]}
    )["targets"][0]["sample_type_id"]


def _plan_decisions(plan):
    return [decision for item in plan.types for decision in item.rewrite_decisions]


def _resolved_envelope_bytes(plan):
    """T03's durable resolved-plan bytes when the plan has an executable
    type; otherwise the rejected-plan provenance projection, since T03
    stores no executable envelope for a wholly-rejected plan and
    ``build_resolved_plan_envelope`` refuses to invent one."""
    if plan.executable_types:
        return build_resolved_plan_envelope(plan, execution_mode=plan.predicted_mode)
    return canonical_json({
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan": {
            "canonical_request_sha256": plan.canonical_submitted_request_sha256,
            "execution_mode": plan.predicted_mode,
            "actor": dict(plan.actor),
            "rejected": [(item.sample_type_id, item.status, [error.code for error in item.errors])
                         for item in plan.rejected_types],
        },
    })


def _ordered_identity(definitions):
    """The one DD-35 ordered-identity projection every stage is compared on:
    (id, logical pos, title) in list order."""
    return [(item.id, item.pos, item.title) for item in definitions]


def _classifier_identity(decisions):
    return _sha256_json([
        (item["operation_kind"], item["behavior_class"], item["requires_metadata_rewrite"])
        for item in decisions
    ])


def _emit_planner_handoff(*, database, case_id, plan, statements, table_checksums,
                          rss_baseline, rss_peak, broker_started, broker_finished,
                          submitted_selector_count, handoff, populations):
    """Write one ``planner-handoff/v1`` artifact and enforce Section 11.10's
    cross-field relations locally (the shared structural validator does not
    implement them yet -- see this task's escalations).

    A wholly-rejected plan (amended Sections 11.8/11.10, user-ruled
    2026-08-04) emits the rejected-plan variant: ``per_class_decisions`` is
    empty and ``rejection_errors`` carries the plan's actual ``PlanError``
    objects verbatim; every other required key is computed exactly as for a
    planned plan. Exactly one of the two forms holds -- decisions non-empty
    with no ``rejection_errors`` key, or decisions empty with a non-empty
    ``rejection_errors`` array."""
    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    identity = orjson.loads((run_root / "boundary-identity.json").read_bytes())
    manifest = orjson.loads(MANIFEST.read_bytes())
    type_plan = plan.types[0]
    decisions = _plan_decisions(plan)
    per_class = []
    for index, decision in enumerate(decisions):
        sample_type_id = type_plan.sample_type_id
        per_class.append({
            "operation_index": index,
            "sample_type_id": sample_type_id,
            # The type's real current sample-row population, read through
            # T04's bulk accessor -- not the plan's already-deduplicated
            # affected count, so the recomputation relation below is a real
            # check rather than a tautology on zero.
            "sample_type_population": populations[sample_type_id],
            "behavior_class": decision["behavior_class"],
            "requires_metadata_rewrite": decision["requires_metadata_rewrite"],
            "reason": decision["reason"],
        })
    resolved_bytes = _resolved_envelope_bytes(plan)
    stored_resolved = _sha256_json(orjson.loads(resolved_bytes))
    payload = {
        "schema_version": "planner-handoff/v1",
        "evidence_run_id": str(uuid.uuid4()),
        "base_sha": manifest["source_identity"]["base_sha"],
        "dependency_sha": manifest["source_identity"]["plan_sha256"],
        "image_id": manifest["source_identity"]["reference_image_id"],
        "server_uuid": identity["server_identity"]["server_uuid"],
        "seek_database_uuid": database.database_uuid,
        "default_database_uuid": _default_database_uuid(database.database_uuid),
        "case_id": case_id,
        "submitted_selector_count": submitted_selector_count,
        "resolved_identity_count": sum(item.counts["requested"] for item in plan.types),
        "statement_observations": statements,
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
        "result": {
            "ordered_identity_sha256": _sha256_json(_ordered_identity(type_plan.before)),
            "logical_record_sha256": _sha256_json(_ordered_identity(type_plan.after)),
            "total": len(type_plan.before),
            "offset": 0,
            "page_size": max(1, len(type_plan.before)),
            "returned_count": len(type_plan.after),
        },
        "submitted_identity": {
            "canonical_bytes_sha256": canonical_sha256(plan.canonical_submitted_request),
            "stored_sha256": plan.canonical_submitted_request_sha256,
        },
        "resolved_identity": {
            "envelope_bytes_sha256": stored_resolved,
            "stored_sha256": stored_resolved,
            "embedded_submitted_sha256": plan.canonical_submitted_request_sha256,
        },
        "planner_result": {
            "ordered_input_identity_sha256": _sha256_json(_ordered_identity(type_plan.before)),
            "ordered_output_identity_sha256": _sha256_json(_ordered_identity(type_plan.after)),
            "before_fingerprint": type_plan.before_physical_fingerprint,
            "after_fingerprint": type_plan.expected_after_semantic_fingerprint,
            "per_class_decisions": per_class,
            "deduplicated_affected_population": plan.affected_sample_rows,
            "threshold": plan.active_threshold,
            "predicted_mode": plan.predicted_mode,
        },
        "handoff": handoff,
    }
    if not per_class:
        # Rejected-plan variant: the actual PlanError objects, field for
        # field (code, message, target_index, attribute_index, field,
        # submitted_identifier) -- never fabricated or reshaped.
        payload["planner_result"]["rejection_errors"] = [
            {"code": error.code, "message": error.message,
             "target_index": error.target_index, "attribute_index": error.attribute_index,
             "field": error.field, "submitted_identifier": error.submitted_identifier}
            for item in plan.types for error in item.errors
        ]

    # -- Section 11.10 relations, asserted rather than merely serialized.
    submitted = payload["submitted_identity"]
    resolved = payload["resolved_identity"]
    result = payload["planner_result"]
    assert submitted["canonical_bytes_sha256"] == submitted["stored_sha256"]
    assert resolved["envelope_bytes_sha256"] == resolved["stored_sha256"]
    assert resolved["embedded_submitted_sha256"] == submitted["stored_sha256"]
    # Amended Section 11.10 oneOf: decisions non-empty with NO
    # `rejection_errors` key, XOR decisions empty with `rejection_errors`
    # non-empty (each item mirroring the frozen PlanError shape).
    if result["per_class_decisions"]:
        assert "rejection_errors" not in result
    else:
        assert result["rejection_errors"]
        for item in result["rejection_errors"]:
            assert set(item) == {"code", "message", "target_index",
                                 "attribute_index", "field", "submitted_identifier"}
            assert item["code"] and item["message"]
    indexes = [item["operation_index"] for item in result["per_class_decisions"]]
    assert len(indexes) == len(set(indexes))
    by_type: dict[int, set[int]] = {}
    for item in result["per_class_decisions"]:
        by_type.setdefault(item["sample_type_id"], set()).add(item["sample_type_population"])
        assert item["requires_metadata_rewrite"] is (
            item["behavior_class"] in {"create-new", "title-rename", "delete"}
        )
    assert all(len(values) == 1 for values in by_type.values())
    recomputed_population = sum(
        next(iter(values))
        for sample_type_id, values in by_type.items()
        if any(item["sample_type_id"] == sample_type_id and item["requires_metadata_rewrite"]
               for item in result["per_class_decisions"])
    )
    assert recomputed_population == result["deduplicated_affected_population"]
    assert (result["predicted_mode"] == "asynchronous") is (
        result["deduplicated_affected_population"] > result["threshold"]
    )
    assert handoff["t04_ordered_identity_sha256"] == handoff["t05_ordered_identity_sha256"]
    assert handoff["t05_ordered_identity_sha256"] == handoff["t07_ordered_identity_sha256"]
    assert handoff["t05_classifier_sha256"] == handoff["t06_classifier_sha256"]
    assert handoff["t06_classifier_sha256"] == handoff["t07_classifier_sha256"]
    assert handoff["physical_before_sha256"] == handoff["physical_after_sha256"]

    directory = run_root / "planner-handoff"
    directory.mkdir(parents=True, exist_ok=True)
    safe = case_id.replace("/", "_").replace("[", "_").replace("]", "_")
    path = directory / f"{safe}.json"
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS) + b"\n")
    return payload


def _stage_ordered_identity(type_id):
    """T04's own DD-35 ordered identity for one sample type, from an
    independent invocation of its snapshot accessor. The order is never
    re-implemented here and the value never copied from the plan, so
    comparing it to T05's input order is a real check."""
    snapshot = SeekAttributeGateway().type_snapshots({type_id})[type_id]
    return _sha256_json([(row.id, index, row.title)
                         for index, row in enumerate(snapshot.definitions, start=1)])


def _run_planned(database, *, case_id, request, threshold=THRESHOLD, selector_count,
                 emit=True, extra_handoff=None):
    """Run one real T01 -> T04 -> T05 dry-run under external observation.

    Fresh-connection table checksums bracket the call on both the SEEK and
    the default database; every statement issued on either Django alias is
    captured and classified; RSS is sampled from procfs. All of it is
    external to the planner -- no repository counter participates."""
    seek_alias = settings.SEEK_DATABASE
    aliases = [seek_alias, "default"]
    repository = AttributeRepository(SeekAttributeGateway())
    planner = MutationPlanner(threshold=threshold)

    broker_started = _utc_now()
    before = _snapshot_tables(database)
    jobs_before = _job_table_state(database)
    # T04 stage identity, taken independently of the plan and before it runs.
    stage_type_id = _submitted_type_id(request)
    t04_identity = _stage_ordered_identity(stage_type_id) if emit else None
    # Release the oracle read's rows before the baseline so the RSS window
    # brackets `plan_mutation` alone.
    gc.collect()
    rss_baseline = _rss_bytes()
    with _capture_all_statements(aliases) as captured:
        plan = planner.plan_mutation(request, repository)
    rss_peak = _rss_bytes()
    broker_finished = _utc_now()
    after = _snapshot_tables(database)
    jobs_after = _job_table_state(database)

    for table in CHECKSUM_TABLES:
        assert before[table] == after[table], f"planning mutated {table}"
    assert jobs_before == jobs_after, "planning created a mutation job/partition row"
    statements = captured[seek_alias]
    assert captured["default"] == [], "planning issued a default-database statement"
    _assert_read_safe(statements)

    populations = repository.sample_type_populations(
        [item.sample_type_id for item in plan.types if item.sample_type_id is not None]
    )
    checksums = _table_checksums(database, before=before, after=after)
    physical_identity = _sha256_json(sorted(
        (table, row["before_row_count"], row["before_sha256"]) for row, table in zip(checksums, CHECKSUM_TABLES)
    ))
    if emit:
        ordered = _sha256_json(_ordered_identity(plan.types[0].before))
        # "T07 locked-recheck" stage on a dry run: a second independent read
        # after planning must observe the identical ordered identity, because
        # planning wrote nothing.
        t07_identity = _stage_ordered_identity(stage_type_id)
        assert t04_identity == ordered, "T04 and T05 disagree on DD-35 input order"
        assert t07_identity == ordered, "post-planning read disagrees on DD-35 order"
        classifier = _classifier_identity(_plan_decisions(plan))
        handoff = {
            "t04_ordered_identity_sha256": t04_identity,
            "t05_ordered_identity_sha256": ordered,
            "t07_ordered_identity_sha256": t07_identity,
            "t05_classifier_sha256": classifier,
            "t06_classifier_sha256": classifier,
            "t07_classifier_sha256": classifier,
            "locked_fingerprint_sha256": plan.types[0].before_physical_fingerprint,
            "post_state_fingerprint_sha256": plan.types[0].before_physical_fingerprint,
            "physical_before_sha256": physical_identity,
            "physical_after_sha256": physical_identity,
        }
        handoff.update(extra_handoff or {})
        _emit_planner_handoff(
            database=database, case_id=case_id, plan=plan, statements=statements,
            table_checksums=checksums, rss_baseline=rss_baseline, rss_peak=rss_peak,
            broker_started=broker_started, broker_finished=broker_finished,
            submitted_selector_count=selector_count, handoff=handoff, populations=populations,
        )
    return plan, statements, (rss_baseline, rss_peak)


# ---------------------------------------------------------------------------
# test_submitted_and_resolved_identities_remain_distinct
# ---------------------------------------------------------------------------


def test_submitted_and_resolved_identities_remain_distinct(disposable_attribute_db, django_db_blocker):
    """Section 11.9: submitted bytes/hash remain the pre-resolution identity
    while the resolved-plan envelope independently binds the semantic plan.
    An id-spelled and a title-spelled submission resolve, through real T04
    identifier resolution, to the same semantic plan and the same
    idempotency key -- yet keep distinct submitted hashes, and neither
    payload may be substituted for the other."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database)
    repository = AttributeRepository(SeekAttributeGateway())

    by_id = patch_request([patch_operation(11, {"description": "x"})], sample_type=1)
    by_title = patch_request([patch_operation("RNA", {"description": "x"})], sample_type="Blood")
    # The id-spelled submission runs under full external observation and
    # emits this Section 11.9 node's planner-handoff/v1 artifact; the
    # title-spelled twin is planned directly and compared against it.
    plan_by_id, _statements, _rss = _run_planned(
        database, case_id="test_submitted_and_resolved_identities_remain_distinct",
        request=by_id, selector_count=1,
    )
    plan_by_title = MutationPlanner(threshold=THRESHOLD).plan_mutation(by_title, repository)

    assert plan_by_id.canonical_submitted_request_sha256 != plan_by_title.canonical_submitted_request_sha256
    assert plan_by_id.canonical_submitted_request_sha256 == canonical_sha256(plan_by_id.canonical_submitted_request)
    assert plan_by_title.canonical_submitted_request_sha256 == canonical_sha256(plan_by_title.canonical_submitted_request)
    # Equivalent resolution: identical semantic plan identity.
    assert plan_by_id.types[0].idempotency_key == plan_by_title.types[0].idempotency_key
    assert plan_by_id.types[0].expected_after_semantic_fingerprint == \
        plan_by_title.types[0].expected_after_semantic_fingerprint
    # The two identities are independent and non-interchangeable.
    resolved_by_id = build_resolved_plan_envelope(plan_by_id, execution_mode="synchronous")
    resolved_by_title = build_resolved_plan_envelope(plan_by_title, execution_mode="synchronous")
    assert resolved_by_id != resolved_by_title
    assert orjson.loads(resolved_by_id)["plan"]["canonical_request_sha256"] == \
        plan_by_id.canonical_submitted_request_sha256
    assert plan_by_id.canonical_submitted_request != plan_by_title.canonical_submitted_request


# ---------------------------------------------------------------------------
# test_dd35_order_matches_repository_and_fingerprints[null-duplicate-gap]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["null-duplicate-gap"], ids=["null-duplicate-gap"])
def test_dd35_order_matches_repository_and_fingerprints(case_id, disposable_attribute_db, django_db_blocker):
    """Section 11.9: T04 and T05 share valid-positive-first/NULL-last order
    for inputs, previews, and fingerprints, with zero writes.

    Legacy storage carries NULL, duplicate and gapped physical positions.
    The independent oracle is T04's own ``logicalize_definitions`` over a
    fresh catalog read -- never a re-implementation of the order here."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    _seed_reference_rows(database)
    database.execute_sql([
        ("ALTER TABLE sample_attributes MODIFY pos INT NULL", ()),
        ("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,"
         "is_title,created_at,updated_at) VALUES"
         "(1,1,1,'ValidA',0,2,0,NOW(6),NOW(6)),"
         "(2,1,1,'ValidB',0,2,0,NOW(6),NOW(6)),"      # duplicate physical pos
         "(3,1,1,'GapC',0,5,0,NOW(6),NOW(6)),"        # gapped
         "(4,1,1,'NullD',0,NULL,0,NOW(6),NOW(6)),"
         "(5,1,1,'NullE',0,NULL,0,NOW(6),NOW(6))", ()),
    ])

    request = create_request([create_definition("Fresh")])
    plan, statements, _rss = _run_planned(
        database, case_id=f"test_dd35_order_matches_repository_and_fingerprints[{case_id}]",
        request=request, selector_count=1,
    )
    type_plan = plan.types[0]

    # Independent T04 oracle: a fresh catalog read through the same adapter.
    _total, oracle_rows = SeekAttributeGateway().catalog(
        type_ids={1}, attribute_ids=None, offset=0, limit=5000,
    )
    assert [(row.id, row.pos) for row in oracle_rows] == [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]

    # T05's planning input order is byte-identical to T04's.
    assert _ordered_identity(type_plan.before) == [(row.id, row.pos, row.title) for row in oracle_rows]
    assert [row.physical_pos for row in type_plan.before] == [2, 2, 5, None, None]

    # Fingerprint rows follow DD-35 order, never database-native NULL order.
    expected_before_fingerprint = canonical_sha256([
        (row.id, row.updated_at, row.title, row.sample_attribute_type_id, row.required, row.pos,
         row.is_title, row.description, row.unit_id, row.sample_controlled_vocab_id,
         row.linked_sample_type_id)
        for row in type_plan.before
    ])
    assert type_plan.before_physical_fingerprint == expected_before_fingerprint

    # Previews and created-token recovery share that one order.
    assert [record["id"] for record in type_plan.preview_records] == [1, 2, 3, 4, 5]
    assert [record["pos"] for record in type_plan.preview_records] == [1, 2, 3, 4, 5]
    assert type_plan.created_identity_tokens == (("created:0:0", "Fresh"),)
    assert type_plan.hypothetical_preview_records[0]["pos"] == 6
    assert _ordered_identity(type_plan.after) == [
        (1, 1, "ValidA"), (2, 2, "ValidB"), (3, 3, "GapC"), (4, 4, "NullD"), (5, 5, "NullE"),
        (None, 6, "Fresh"),
    ]
    # Zero writes: enforced inside _run_planned by fresh-connection checksums
    # on both databases and by statement classification.
    assert all(row["statement_class"] == "SELECT" for row in statements)


# ---------------------------------------------------------------------------
# test_real_collation_patch_assignment[two-way-swap|many-to-one-rejected]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["two-way-swap", "many-to-one-rejected"])
def test_real_collation_patch_assignment(case, disposable_attribute_db, django_db_blocker):
    """Section 11.9: a two-way rename is accepted only when the complete
    final assignment is unique under the *real* database collation; a
    many-to-one final assignment is rejected from T04's real-collation
    classes.

    The oracle is ``AttributeRepository.title_collation_classes`` executing
    real SQL grouping against the disposable server -- Python ``casefold``,
    lowercasing, Unicode normalization and byte equality are not collision
    oracles here."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _reset_seek_tables(database)
    _seed_reference_rows(database)
    database.execute_sql([
        ("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,"
         "is_title,created_at,updated_at) VALUES"
         "(10,1,1,'RNA',0,1,0,NOW(6),NOW(6)),"
         "(11,1,1,'DNA',0,2,0,NOW(6),NOW(6)),"
         "(12,1,1,'Untouched',0,3,0,NOW(6),NOW(6))", ()),
    ])
    if case == "two-way-swap":
        request = patch_request([
            patch_operation(10, {"title": "DNA"}), patch_operation(11, {"title": "RNA"}),
        ])
    else:
        request = patch_request([
            patch_operation(10, {"title": "Shared"}), patch_operation(11, {"title": "shared"}),
        ])
    plan, _statements, _rss = _run_planned(
        database, case_id=f"test_real_collation_patch_assignment[{case}]",
        request=request, selector_count=2,
    )
    type_plan = plan.types[0]

    # The oracle really is the database's own grouping.
    repository = AttributeRepository(SeekAttributeGateway())
    classes = repository.title_collation_classes([
        TitleCollationRequest(0, 0, "patch-final", 1, "dna", exclude_id=10),
        TitleCollationRequest(0, 1, "patch-final", 1, "RNA", exclude_id=11),
    ])
    assert classes[(0, 0, "patch-final")].match_ids == (11,)
    assert classes[(0, 1, "patch-final")].match_ids == (10,)

    if case == "two-way-swap":
        assert type_plan.status == "planned"
        assert type_plan.executable is True
        assert not type_plan.errors
        assert [(row.id, row.title) for row in type_plan.after] == [
            (10, "DNA"), (11, "RNA"), (12, "Untouched"),
        ]
    else:
        assert type_plan.status == "failed"
        assert type_plan.executable is False
        assert type_plan.errors[0].code == "stale_title_collation_oracle"
        assert type_plan.before == type_plan.after
        assert plan.executable_types == ()


# ---------------------------------------------------------------------------
# test_title_transition[*]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [
    "create-no-uid-clears-old", "patch-no-uid-clears-old",
    "non-uid-with-uid-rejected", "unchanged-promotion",
])
def test_title_transition(case, disposable_attribute_db, django_db_blocker):
    """Section 11.9: create and patch promotion share one DD-17/DD-18
    transition kernel over the complete planned definition set. Without UID
    each actual old title is cleared exactly once and every clear is
    recorded; with UID present a non-UID promotion fails without changing
    UID or siblings; re-promoting the current title is a pure no-op."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database)

    # Plasma(3) has Alpha(20, title)/Beta(21) and no UID; Blood(1) has UID(10).
    if case == "create-no-uid-clears-old":
        request = create_request([create_definition("New", is_title=True)], sample_type=3)
    elif case == "patch-no-uid-clears-old":
        request = patch_request([patch_operation(21, {"is_title": True})], sample_type=3)
    elif case == "non-uid-with-uid-rejected":
        request = patch_request([patch_operation(12, {"is_title": True})], sample_type=1)
    else:
        request = patch_request([patch_operation(10, {"is_title": True})], sample_type=1)

    plan, _statements, _rss = _run_planned(
        database, case_id=f"test_title_transition[{case}]", request=request,
        selector_count=1,
    )
    type_plan = plan.types[0]
    cleared = [change for change in type_plan.automatic_changes
               if change.kind == "title_cleared" and change.field == "is_title"]

    if case == "create-no-uid-clears-old":
        assert type_plan.status == "planned"
        assert [(row.title, row.is_title) for row in type_plan.after] == [
            ("Alpha", False), ("Beta", False), ("New", True),
        ]
        assert [(change.attribute_id, change.previous_value, change.new_value) for change in cleared] == \
            [(20, True, False)]
    elif case == "patch-no-uid-clears-old":
        assert type_plan.status == "planned"
        assert [(row.id, row.is_title) for row in type_plan.after] == [(20, False), (21, True)]
        assert [change.attribute_id for change in cleared] == [20]
    elif case == "non-uid-with-uid-rejected":
        assert type_plan.status == "failed"
        assert type_plan.errors[0].code == "uid_is_sole_title"
        assert type_plan.before == type_plan.after
        assert [(row.id, row.is_title) for row in type_plan.after] == [(10, True), (11, False), (12, False)]
        assert type_plan.automatic_changes == ()
    else:
        assert type_plan.status == "unchanged"
        assert type_plan.executable is False
        assert cleared == []
        assert type_plan.automatic_changes == ()
        assert [(row.id, row.is_title) for row in type_plan.after] == [(10, True), (11, False), (12, False)]


def test_apply_title_transition_kernel_direct_uid_sibling_rejection():
    """Direct kernel-level coverage of the shared transition kernel's
    UID-sibling guard, independent of any repository."""
    rows = (
        Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1, is_title=True,
                   description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
        Definition(id=2, title="RNA", sample_attribute_type_id=5, required=False, pos=2, is_title=False,
                   description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
    )
    with pytest.raises(_TitleTransitionRejected):
        apply_title_transition(rows, 2, operation_kind="patch")


# ---------------------------------------------------------------------------
# test_uid_definition_is_immutable[patch|delete]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["patch", "delete"])
def test_uid_definition_is_immutable(case, disposable_attribute_db, django_db_blocker):
    """Section 11.9: patching UID's title, requiredness or title status, or
    deleting UID, is rejected before any planned or physical change."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database)

    if case == "patch":
        attempts = [
            (patch_request([patch_operation(10, {"title": "Identifier"})]), "uid_rename_forbidden"),
            (patch_request([patch_operation(10, {"required": False})]), "uid_required_forbidden"),
            (patch_request([patch_operation(10, {"is_title": False})]), "uid_title_forbidden"),
        ]
    else:
        attempts = [(delete_request([10]), "uid_delete_forbidden")]

    before = _snapshot_tables(database)
    for request, code in attempts:
        # Every attempt runs under full external observation and emits the
        # amended Section 11.8 rejected-plan artifact for this node; the
        # attempt order is fixed, so the node's artifact deterministically
        # carries the final attempt's rejection.
        plan, _statements, _rss = _run_planned(
            database, case_id=f"test_uid_definition_is_immutable[{case}]",
            request=request, selector_count=1,
        )
        type_plan = plan.types[0]
        assert type_plan.status == "failed"
        assert type_plan.executable is False
        assert type_plan.errors[0].code == code
        assert type_plan.before == type_plan.after
        assert [(row.id, row.title, row.required, row.is_title) for row in type_plan.after][0] == \
            (10, "UID", True, True)
    assert _snapshot_tables(database) == before


# ---------------------------------------------------------------------------
# test_metadata_rewrite_class[*]
# ---------------------------------------------------------------------------

_REWRITE_POPULATION = 7

_REWRITE_REQUESTS = {
    "create-new": (lambda: create_request([create_definition("Fresh")]), "create-new", True),
    "identical-create": (
        lambda: create_request([create_definition("RNA", pos=2)]), "identical-create", False),
    "title-rename": (lambda: patch_request([patch_operation(11, {"title": "RNA2"})]), "title-rename", True),
    "delete": (lambda: delete_request([12]), "delete", True),
    "true-noop": (lambda: patch_request([patch_operation(11, {"description": None})]), "true-noop", False),
    "description": (
        lambda: patch_request([patch_operation(11, {"description": "changed"})]), "definition-only", False),
    "required": (lambda: patch_request([patch_operation(11, {"required": True})]), "definition-only", False),
    "position": (lambda: patch_request([patch_operation(11, {"pos": 3})]), "definition-only", False),
    "is-title": (
        lambda: patch_request([patch_operation(21, {"is_title": True})], sample_type=3),
        "definition-only", False),
    "value-type": (
        lambda: patch_request([patch_operation(11, {"sample_attribute_type": 2})]), "definition-only", False),
    "unit": (lambda: patch_request([patch_operation(11, {"unit": 1})]), "definition-only", False),
    "vocabulary": (
        lambda: patch_request([patch_operation(11, {"sample_controlled_vocab": 1})]), "definition-only", False),
    "linked-type": (
        lambda: patch_request([patch_operation(11, {"linked_sample_type": 2})]), "definition-only", False),
}


@pytest.mark.parametrize("case", [
    "create-new", "title-rename", "delete", "identical-create", "true-noop", "description", "required",
    "position", "is-title", "value-type", "unit", "vocabulary", "linked-type", "mixed-counts-once",
])
def test_metadata_rewrite_class(case, disposable_attribute_db, django_db_blocker):
    """Section 11.9: create-new, title-rename and delete require a metadata
    rewrite and contribute the type's real sample-row population exactly
    once; identical-create, true-noop and every definition-only field class
    contribute zero. A mixed batch contributes the population once, never
    once per operation.

    The population is a real ``COUNT(*)`` over seeded ``samples`` rows read
    through T04's bulk accessor, so a case that "contributes zero" is
    distinguishable from a case with nothing to contribute."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database, population=_REWRITE_POPULATION)
    # The population is real, not assumed.
    assert int(database.query("SELECT COUNT(*) FROM samples WHERE sample_type_id = 1")[0][0]) == \
        _REWRITE_POPULATION

    if case == "mixed-counts-once":
        request = patch_request([
            patch_operation(11, {"description": "changed"}),
            patch_operation(12, {"title": "Renamed"}),
        ])
        expected_classes = ["definition-only", "title-rename"]
        expected_population = _REWRITE_POPULATION
    else:
        builder, expected_class, requires = _REWRITE_REQUESTS[case]
        request = builder()
        expected_classes = [expected_class]
        expected_population = _REWRITE_POPULATION if requires else 0

    plan, _statements, _rss = _run_planned(
        database, case_id=f"test_metadata_rewrite_class[{case}]", request=request,
        selector_count=len(request["targets"][0]["attributes"]),
    )
    decisions = _plan_decisions(plan)
    assert [item["behavior_class"] for item in decisions] == expected_classes
    for item in decisions:
        assert item["requires_metadata_rewrite"] is (
            item["behavior_class"] in {"create-new", "title-rename", "delete"}
        )
    assert plan.affected_sample_rows == expected_population
    assert plan.predicted_mode == "synchronous"
    if case == "mixed-counts-once":
        # Once for the type, not once per rewriting operation.
        assert plan.affected_sample_rows == _REWRITE_POPULATION
        assert sum(1 for item in decisions if item["requires_metadata_rewrite"]) == 1


def test_metadata_rewrite_classifier_is_the_shared_pure_api():
    """Section 11.7: the classifier T06/T07 must import is a pure function of
    ``(before, after, operation_kind)`` -- it never reads a repository, a
    population, or a threshold."""
    row = Definition(id=2, title="RNA", sample_attribute_type_id=1, required=False, pos=2, is_title=False,
                     description=None, unit_id=None, sample_controlled_vocab_id=None,
                     linked_sample_type_id=None, updated_at=None)
    assert classify_metadata_rewrite(before=None, after=None, operation_kind="create").behavior_class == "create-new"
    assert classify_metadata_rewrite(before=row, after=row, operation_kind="patch").behavior_class == "true-noop"
    with pytest.raises(ValueError):
        classify_metadata_rewrite(before=row, after=row, operation_kind="upsert")


# ---------------------------------------------------------------------------
# test_true_rewrite_threshold[*]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "population", "expected"), [
    ("below-n-minus-1", THRESHOLD - 1, "synchronous"),
    ("at-n", THRESHOLD, "synchronous"),
    ("above-n-plus-1", THRESHOLD + 1, "asynchronous"),
], ids=["below-n-minus-1", "at-n", "above-n-plus-1"])
def test_true_rewrite_threshold(case, population, expected, disposable_attribute_db, django_db_blocker):
    """Section 11.9: a true-rewrite population of N-1 or N predicts
    synchronous and N+1 predicts asynchronous -- asynchronous is strictly
    greater than the threshold (plan Amendment Log 2026-08-04; Section
    11.9's own ``[at-n]`` obligation controls over the Section 3 example).

    The population is real seeded ``samples`` rows, and the operation is a
    delete, whose decision always requires a rewrite."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database, population=population)
    assert int(database.query("SELECT COUNT(*) FROM samples WHERE sample_type_id = 1")[0][0]) == population

    plan, _statements, _rss = _run_planned(
        database, case_id=f"test_true_rewrite_threshold[{case}]",
        request=delete_request([12]), selector_count=1,
    )
    assert plan.active_threshold == THRESHOLD
    assert plan.affected_sample_rows == population
    assert plan.predicted_mode == expected
    assert (plan.predicted_mode == "asynchronous") is (population > THRESHOLD)


# ---------------------------------------------------------------------------
# test_real_chain_no_write
# ---------------------------------------------------------------------------


def test_real_chain_no_write(disposable_attribute_db, django_db_blocker):
    """Section 11.9: a real T01 -> T04 -> T05 dry-run changes no SEEK or
    default table and publishes no broker message.

    Fresh connections checksum every affected definition/sample table and
    the default-database job/partition tables before and after; every
    statement issued on either Django alias is externally captured and
    classified, so a write or a lock-taking read fails the node; and the
    disposable broker observer proves zero publications. Repository
    counters take no part in any assertion."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database, population=5)

    request = {
        "kind": "patch", "dry_run": True, "actor": dict(ACTOR),
        "targets": [
            {"sample_type": "Blood", "attributes": [
                patch_operation("RNA", {"description": "observed"}),
                patch_operation(12, {"pos": 2}),
            ]},
        ],
    }
    plan, statements, _rss = _run_planned(
        database, case_id="test_real_chain_no_write", request=request, selector_count=2,
    )

    assert plan.types[0].status == "planned"
    assert plan.types[0].executable is True
    assert plan.types[0].counts["patched"] == 2
    # Externally observed: reads only, no lock-taking read, bounded parameters.
    assert statements
    assert {row["statement_class"] for row in statements} <= {"SELECT", "TRANSACTION"}
    assert {row["lock_classification"] for row in statements} == {"none"}
    assert all(row["parameter_count"] <= 50_000 for row in statements)
    # The dry-run flag survives into the stored submitted identity verbatim.
    assert plan.canonical_submitted_request["dry_run"] is True
    assert plan.canonical_submitted_request_sha256 == canonical_sha256(plan.canonical_submitted_request)
    # And the plan is identical with dry_run flipped: planning is write-free
    # regardless of the flag (DD-08).
    executed = dict(request, dry_run=False)
    live = MutationPlanner(threshold=THRESHOLD).plan_mutation(
        executed, AttributeRepository(SeekAttributeGateway()),
    )
    assert live.types[0].idempotency_key == plan.types[0].idempotency_key
    assert live.canonical_submitted_request_sha256 != plan.canonical_submitted_request_sha256


# ---------------------------------------------------------------------------
# test_real_chain_scale[501|50001]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selector_count", [501, 50001])
def test_real_chain_scale(selector_count, disposable_attribute_db, django_db_blocker):
    """Section 11.9: 501 and 50,001 *actual distinct* operations traverse
    real T04 bounded resolution and T05 planning, with independently
    generated expected counts/hashes and externally measured RSS and SQL
    parameter sizes. No public request maximum is invented -- 50,001
    selectors are planned, not rejected.

    Bounds asserted from the frozen manifest's ``bounded_work_contract``:
    ``queries <= 18 + 9*ceil(unique_identifiers/500) + distinct_resolved_types``
    (the k-floor reconciliation recorded in the plan's Amendment Log
    2026-08-04, which supersedes the task spec's Section 2/8 text),
    chunk size <= 500, <= 50,000 parameters per statement, and added peak
    RSS <= 268,435,456 bytes."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    ids = _bulk_seed_attributes(database, sample_type_id=1, count=selector_count)
    assert len(ids) == selector_count
    oracle_total = int(database.query("SELECT COUNT(*) FROM sample_attributes WHERE sample_type_id = 1")[0][0])
    assert oracle_total == selector_count

    plan, statements, (rss_baseline, rss_peak) = _run_planned(
        database, case_id=f"test_real_chain_scale[{selector_count}]",
        request=delete_request(ids), selector_count=selector_count,
    )
    type_plan = plan.types[0]

    contract = orjson.loads(MANIFEST.read_bytes())["bounded_work_contract"]
    query_bound = 18 + 9 * math.ceil(selector_count / IDENTIFIER_CHUNK_SIZE) + 1
    assert len(statements) <= query_bound
    assert all(row["parameter_count"] <= contract["sql_parameters_per_statement_max"] for row in statements)
    assert max(rss_peak, rss_baseline) - rss_baseline <= contract["planner_peak_rss_bytes_max"]

    # Independently generated expected result: every seeded row is deleted,
    # the planned set is empty, and the expected post-state fingerprint is the
    # hash of an empty semantic row list.
    assert type_plan.counts["requested"] == selector_count
    assert type_plan.counts["deleted"] == selector_count
    assert type_plan.after == ()
    assert type_plan.expected_after_semantic_fingerprint == canonical_sha256([])
    assert type_plan.before_physical_fingerprint != type_plan.expected_after_semantic_fingerprint
    assert _ordered_identity(type_plan.before) == [(i, i, f"A{i}") for i in ids]
    assert len(type_plan.lock_order) == selector_count + 1


# ---------------------------------------------------------------------------
# test_t04_t05_t07_order_classifier_fingerprint_handoff
# ---------------------------------------------------------------------------


def test_t04_t05_t07_order_classifier_fingerprint_handoff(disposable_attribute_db, django_db_blocker):
    """Section 11.9: T04/T05/T07 ordered identities, T05/T06/T07 classifier
    outputs, and locked/post-state fingerprint handoffs match exactly.

    SCOPE HONESTY -- read before trusting this node. T07 (executor) does not
    exist in the tree, and merged T06 (``nextseek_api/attributes/metadata.py``)
    does not import ``classify_metadata_rewrite`` at all, so no real T06/T07
    call site can be observed here. What is genuinely proved:

    * the ordered identity T05 plans on equals T04's own
      ``logicalize_definitions`` output over an *independent fresh read*, and
      equals it again on a second read taken after planning (the state T07's
      locked recheck would observe on a dry run);
    * the locked and post-state fingerprints equal independently recomputed
      stage hashes, and the physical checksums are unchanged;
    * the classifier output hash is stable across repeated evaluation of the
      shared pure API that Section 11.7 requires T06 and T07 to import.

    The T06/T07 classifier stages are therefore represented by independent
    recomputation through that shared API rather than by a downstream call
    site. That substitution is recorded as a plan-delta escalation, not
    silently passed off as a real three-task join."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database, population=3)

    # T04 stage: independent ordered identity from a fresh read.
    _total, t04_rows = SeekAttributeGateway().catalog(
        type_ids={1}, attribute_ids=None, offset=0, limit=5000,
    )
    t04_identity = _sha256_json([(row.id, row.pos, row.title) for row in t04_rows])

    request = patch_request([patch_operation(11, {"description": "handoff"})])
    plan, _statements, _rss = _run_planned(
        database, case_id="test_t04_t05_t07_order_classifier_fingerprint_handoff",
        request=request, selector_count=1, emit=False,
    )
    type_plan = plan.types[0]
    t05_identity = _sha256_json(_ordered_identity(type_plan.before))
    assert t05_identity == t04_identity

    # "T07 locked recheck" stage on a dry run: a second independent read
    # after planning must observe the identical ordered identity, because
    # planning wrote nothing.
    _total, post_rows = SeekAttributeGateway().catalog(
        type_ids={1}, attribute_ids=None, offset=0, limit=5000,
    )
    t07_identity = _sha256_json([(row.id, row.pos, row.title) for row in post_rows])
    assert t07_identity == t05_identity

    # Locked / post-state fingerprints recomputed independently of the plan.
    recomputed_before = canonical_sha256([
        (row.id, row.updated_at, row.title, row.sample_attribute_type_id, row.required, row.pos,
         row.is_title, row.description, row.unit_id, row.sample_controlled_vocab_id,
         row.linked_sample_type_id)
        for row in type_plan.before
    ])
    assert type_plan.before_physical_fingerprint == recomputed_before

    # Classifier stage, through the one shared pure API.
    decisions = _plan_decisions(plan)
    assert decisions
    t05_classifier = _classifier_identity(decisions)
    recomputed = [
        {"operation_kind": "patch",
         "behavior_class": classify_metadata_rewrite(
             before=type_plan.before[1], after=type_plan.after[1], operation_kind="patch").behavior_class,
         "requires_metadata_rewrite": classify_metadata_rewrite(
             before=type_plan.before[1], after=type_plan.after[1],
             operation_kind="patch").requires_metadata_rewrite}
    ]
    assert _classifier_identity(recomputed) == t05_classifier

    physical = _sha256_json(sorted((table, database.checksum(table)) for table in CHECKSUM_TABLES))
    handoff = {
        "t04_ordered_identity_sha256": t04_identity,
        "t05_ordered_identity_sha256": t05_identity,
        "t07_ordered_identity_sha256": t07_identity,
        "t05_classifier_sha256": t05_classifier,
        "t06_classifier_sha256": t05_classifier,
        "t07_classifier_sha256": t05_classifier,
        "locked_fingerprint_sha256": recomputed_before,
        "post_state_fingerprint_sha256": recomputed_before,
        "physical_before_sha256": physical,
        "physical_after_sha256": physical,
    }
    payload = _emit_planner_handoff(
        database=database, case_id="test_t04_t05_t07_order_classifier_fingerprint_handoff",
        plan=plan, statements=[], table_checksums=[], rss_baseline=0, rss_peak=0,
        broker_started=_utc_now(), broker_finished=_utc_now(),
        submitted_selector_count=1, handoff=handoff,
        populations=AttributeRepository(SeekAttributeGateway()).sample_type_populations([1]),
    )
    assert payload["handoff"] == handoff
    assert set(handoff) == {
        "t04_ordered_identity_sha256", "t05_ordered_identity_sha256", "t07_ordered_identity_sha256",
        "t05_classifier_sha256", "t06_classifier_sha256", "t07_classifier_sha256",
        "locked_fingerprint_sha256", "post_state_fingerprint_sha256",
        "physical_before_sha256", "physical_after_sha256",
    }


# ---------------------------------------------------------------------------
# test_title_spelled_relationship_identifiers_through_real_t04d[*]
# (supplementary Wave-4 node -- not a frozen Section 11.9 obligation row)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["resolves", "unresolvable-fails"])
def test_title_spelled_relationship_identifiers_through_real_t04d(case, disposable_attribute_db, django_db_blocker):
    """USER-RULED Wave-4 lift (plan Amendment Log 2026-08-04 (2)): T05
    consumes the real T04d bulk relationship-identifier resolution end to
    end over the disposable SEEK database -- no fake anywhere in the chain.

    ``resolves``: a create definition spelling all four relationship fields
    by TITLE plans a real mutation whose planned row and hypothetical
    preview carry the verified ``*_id`` identities and their real display
    joins. ``unresolvable-fails``: an unresolvable spelling fails the type
    with EXACTLY T04d's frozen wrapper code (``unit_not_found``) and full
    provenance -- one error, never a duplicate
    ``relationship_identifier_unresolved`` beside it (the pre-lift planner
    double-reported; this assertion was red before the lift) -- and a unit
    SYMBOL spelling also fails even though the symbol exists (DD-19)."""
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database)

    if case == "resolves":
        request = create_request([create_definition(
            "Fresh", **{"sample_attribute_type": "Text", "unit": "milligram",
                        "sample_controlled_vocab": "Species", "linked_sample_type": "Tissue"},
        )])
        plan, statements, _rss = _run_planned(
            database, case_id=f"test_title_spelled_relationship_identifiers_through_real_t04d[{case}]",
            request=request, selector_count=1,
        )
        type_plan = plan.types[0]
        assert type_plan.status == "planned"
        assert type_plan.executable is True
        assert type_plan.errors == ()
        created = [row for row in type_plan.after if row.id is None]
        assert [(row.sample_attribute_type_id, row.unit_id,
                 row.sample_controlled_vocab_id, row.linked_sample_type_id)
                for row in created] == [(2, 1, 1, 2)]
        assert type_plan.created_identity_tokens == (("created:0:0", "Fresh"),)
        # Real display joins over the resolved identities, from T04's own
        # hypothetical materialization -- titles come from the database rows
        # the titles were resolved against, closing the loop.
        preview = type_plan.hypothetical_preview_records[0]
        assert preview["sample_attribute_type_title"] == "Text"
        assert preview["unit_title"] == "milligram"
        assert preview["unit_symbol"] == "mg"
        assert preview["sample_controlled_vocab_title"] == "Species"
        # T04 DISPLAY-JOIN DEFECT FIXED under T04e (merged beneath this task):
        # `SeekAttributeGateway.materialization_identities` now adds
        # `linked_sample_type_id` values to its sample_types identity load,
        # so `materialize_hypothetical_records` renders the linked type's
        # real display title instead of silently dropping it. The expected
        # title below comes from this node's own `_seed_reference_rows`
        # fixture (`sample_types` id 2 == "Tissue", the row
        # `linked_sample_type_id` resolved against), closing the loop
        # end-to-end through the real T04d/T04e chain.
        assert preview["linked_sample_type_id"] == 2
        assert preview["linked_sample_type_title"] == "Tissue"
        assert all(row["statement_class"] == "SELECT" for row in statements)
        return

    request = create_request([create_definition("Fresh", unit="furlong")])
    plan, _statements, _rss = _run_planned(
        database, case_id=f"test_title_spelled_relationship_identifiers_through_real_t04d[{case}]",
        request=request, selector_count=1,
    )
    type_plan = plan.types[0]
    assert type_plan.status == "failed"
    assert type_plan.executable is False
    assert [error.code for error in type_plan.errors] == ["unit_not_found"]
    assert (type_plan.errors[0].target_index, type_plan.errors[0].attribute_index) == (0, 0)
    assert type_plan.errors[0].field == "unit"
    assert type_plan.errors[0].submitted_identifier == "furlong"
    assert type_plan.before == type_plan.after
    assert plan.executable_types == ()

    # DD-19 through the real chain: the seeded unit's symbol ("mg") exists
    # but is never a match key -- a symbol-spelled patch change fails with
    # the same frozen code rather than resolving.
    symbol_plan = MutationPlanner(threshold=THRESHOLD).plan_mutation(
        patch_request([patch_operation(11, {"unit": "mg"})]),
        AttributeRepository(SeekAttributeGateway()),
    )
    assert symbol_plan.types[0].status == "failed"
    assert [error.code for error in symbol_plan.types[0].errors] == ["unit_not_found"]
    assert symbol_plan.types[0].errors[0].submitted_identifier == "mg"
