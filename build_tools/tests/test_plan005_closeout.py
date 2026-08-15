"""Focused tests for the Plan 005 16-row closeout protocol."""
from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from build_tools.plan005_closeout import (
    PROTOCOL_RECORD_IDS,
    ProtocolError,
    load_schema,
    main,
    protocol_manifest,
    protocol_rows,
    validate_protocol_rows,
)


def test_protocol_ids_are_exactly_the_locked_16_in_order():
    rows = protocol_rows()
    assert tuple(row["id"] for row in rows) == PROTOCOL_RECORD_IDS
    assert PROTOCOL_RECORD_IDS[0] == "01-baseline"
    assert PROTOCOL_RECORD_IDS[-1] == "16-final-gate"
    validate_protocol_rows(rows)


def test_schema_pins_the_same_16_ids():
    schema = load_schema()
    consts = [item["const"] for item in schema["properties"]["record_ids"]["prefixItems"]]
    assert tuple(consts) == PROTOCOL_RECORD_IDS
    assert schema["properties"]["rows"]["minItems"] == 16
    assert schema["properties"]["rows"]["maxItems"] == 16


def test_extra_row_is_red():
    rows = protocol_rows()
    extra = deepcopy(rows[-1])
    extra["id"] = "17-bonus"
    with pytest.raises(ProtocolError, match="row count"):
        validate_protocol_rows(rows + [extra])


def test_missing_row_is_red():
    rows = protocol_rows()[:-1]
    with pytest.raises(ProtocolError, match="row count"):
        validate_protocol_rows(rows)


def test_renamed_row_is_red():
    rows = protocol_rows()
    rows[4]["id"] = "05-future-ops"
    with pytest.raises(ProtocolError, match="mismatch"):
        validate_protocol_rows(rows)


def test_duplicate_row_is_red():
    rows = protocol_rows()
    rows[3] = deepcopy(rows[2])
    with pytest.raises(ProtocolError, match="duplicate"):
        validate_protocol_rows(rows)


def test_out_of_order_is_red():
    rows = protocol_rows()
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ProtocolError, match="out of order"):
        validate_protocol_rows(rows)


def test_network_enablement_is_red():
    rows = protocol_rows()
    rows[1]["network"] = "bridge"
    with pytest.raises(ProtocolError, match="network"):
        validate_protocol_rows(rows)


def test_cli_protocol_json(capsys):
    assert main(["protocol", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["record_ids"] == list(PROTOCOL_RECORD_IDS)


def test_cli_preflight_requires_paths():
    assert main(["preflight"]) == 2


def test_pytest_lanes_mount_evidence_and_django_env():
    rows = {row["id"]: row["argv_template"] for row in protocol_rows()}
    for record_id, junit in (
        ("05-future-op", "--junitxml=/evidence/future-op.junit.xml"),
        ("06-audit-a", "--junitxml=/evidence/audit-a.junit.xml"),
        ("07-assistant-route", "--junitxml=/evidence/assistant-route.junit.xml"),
        ("08-build-tools", "--junitxml=/evidence/build-tools.junit.xml"),
    ):
        argv = rows[record_id]
        assert "{writable}:/evidence" in argv
        assert junit in argv
        assert "PYTHONPATH=/repo:/repo/dmac_assistant/src:/repo/chat_nextseek/src" in argv
        assert "/home/taishajo/work/NExtSEEK/.git:/home/taishajo/work/NExtSEEK/.git:ro" in argv
    for record_id in ("05-future-op", "06-audit-a", "07-assistant-route"):
        assert "DJANGO_SETTINGS_MODULE=dmac.test_settings" in rows[record_id]
    assert "DJANGO_SETTINGS_MODULE=dmac.test_settings" not in rows["08-build-tools"]


def test_coverage_run_mounts_pinned_paired_zip_read_only():
    from build_tools.plan005_closeout import PINNED_PAIRED_ZIP_VOLUME

    argv = next(row["argv_template"] for row in protocol_rows() if row["id"] == "12-coverage-run")
    assert PINNED_PAIRED_ZIP_VOLUME in argv
    assert PINNED_PAIRED_ZIP_VOLUME.endswith(":ro")


def _synthetic_records(evidence_root, repo, head, tree, start="2026-08-15T18:00:00+00:00"):
    from datetime import datetime, timedelta, timezone

    from build_tools.plan005_closeout import (
        COMMAND_TIMEOUT_SECONDS,
        IMMUTABLE_NEXTSEEK_IMAGE,
        SEQUENCE_BUDGET_SECONDS,
        protocol_rows,
    )
    from build_tools.plan005_closeout_control import expand_argv

    t0 = datetime.fromisoformat(start)
    rows = protocol_rows()
    records = []
    before = {}
    for index, proto in enumerate(rows):
        writable = str(evidence_root / proto["declared_output_namespace"])
        argv = expand_argv(
            proto["argv_template"],
            repo=str(repo),
            image=proto["image"] or IMMUTABLE_NEXTSEEK_IMAGE,
            evidence_root=str(evidence_root),
            candidate=head,
            writable=writable,
        )
        rec_prefix = f"records/{proto['id']}/record.json"
        art_prefix = f"{proto['declared_output_namespace']}/out.bin"
        after = dict(before)
        after[rec_prefix] = f"{index:064x}"
        after[art_prefix] = f"{index+100:064x}"
        stamp = (t0 + timedelta(seconds=index)).replace(tzinfo=timezone.utc).isoformat()
        end = (t0 + timedelta(seconds=index + 1)).replace(tzinfo=timezone.utc).isoformat()
        records.append(
            {
                "name": proto["id"],
                "argv": argv,
                "exit_code": 0,
                "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                "sequence_budget_seconds": SEQUENCE_BUDGET_SECONDS,
                "start_time": stamp,
                "end_time": end,
                "pre": {"head": head, "tree": tree, "porcelain": "", "branch": "ultraplan/plan005-op-registry"},
                "post": {"head": head, "tree": tree, "porcelain": "", "branch": "ultraplan/plan005-op-registry"},
                "evidence_root_before": dict(before),
                "evidence_root_after": dict(after),
            }
        )
        before = dict(after)
    return records


def test_control_mounts_require_worktree_git_mirror_and_ro_aggregate():
    from build_tools.plan005_closeout_control import CloseoutError, assert_control_stage_mounts

    repo = "/home/taishajo/work/NExtSEEK-plan005"
    argv = [
        "docker", "run", "--rm", "--network", "none",
        "-e", "GIT_CONFIG_COUNT=1",
        "-e", "GIT_CONFIG_KEY_0=safe.directory",
        "-e", f"GIT_CONFIG_VALUE_0={repo}",
        "-v", f"{repo}:{repo}:ro",
        "-v", "/home/taishajo/work/NExtSEEK/.git:/home/taishajo/work/NExtSEEK/.git:ro",
        "-v", "/home/taishajo/work/NExtSEEK-dev:/home/taishajo/work/NExtSEEK-dev:ro",
        "-v", "/tmp/head:/all-evidence:ro",
        "-v", "/tmp/vet:/vet-reports:ro",
        "-v", "/tmp/head/control/preflight:/control-output",
        "sha256:879406139db3581c6f1b040a5bdcef40385a62780af01e71d2766003e3745a81",
        "python",
    ]
    assert_control_stage_mounts(argv, stage="preflight")
    bad = list(argv)
    bad[bad.index("/tmp/head:/all-evidence:ro")] = "/tmp/head:/all-evidence"
    with pytest.raises(CloseoutError, match="writable /all-evidence"):
        assert_control_stage_mounts(bad, stage="preflight")


def test_protocol_binding_rejects_omit_duplicate_rename_reorder_and_alt_producer(tmp_path):
    from build_tools.plan005_closeout_control import CloseoutError, validate_protocol_binding

    evidence = tmp_path / "abc123"
    evidence.mkdir()
    repo = "/home/taishajo/work/NExtSEEK-plan005"
    records = _synthetic_records(evidence, repo, "abc123", "tree1")
    validate_protocol_binding(records, repo=repo, evidence_root=evidence, candidate="abc123")

    omitted = records[:-1]
    with pytest.raises(CloseoutError, match="missing"):
        validate_protocol_binding(omitted, repo=repo, evidence_root=evidence, candidate="abc123")

    dup = records + [deepcopy(records[-1])]
    with pytest.raises(CloseoutError, match="duplicate"):
        validate_protocol_binding(dup, repo=repo, evidence_root=evidence, candidate="abc123")

    renamed = deepcopy(records)
    renamed[4]["name"] = "05-future-ops"
    with pytest.raises(CloseoutError, match="unexpected"):
        validate_protocol_binding(renamed, repo=repo, evidence_root=evidence, candidate="abc123")

    reordered = deepcopy(records)
    reordered[0]["start_time"], reordered[1]["start_time"] = (
        reordered[1]["start_time"],
        reordered[0]["start_time"],
    )
    with pytest.raises(CloseoutError, match="out of order"):
        validate_protocol_binding(reordered, repo=repo, evidence_root=evidence, candidate="abc123")

    alt = deepcopy(records)
    alt[-1]["argv"] = [tok.replace(str(evidence), str(tmp_path / "other")) for tok in alt[-1]["argv"]]
    with pytest.raises(CloseoutError, match="argv"):
        validate_protocol_binding(alt, repo=repo, evidence_root=evidence, candidate="abc123")


def test_span_and_timestamp_mutations(tmp_path):
    from build_tools.plan005_closeout_control import CloseoutError, validate_span

    evidence = tmp_path / "abc123"
    evidence.mkdir()
    records = _synthetic_records(
        evidence,
        "/repo",
        "abc123",
        "tree1",
        start="2026-08-15T18:00:00+00:00",
    )
    assert validate_span(records) < 3600
    wide = deepcopy(records)
    wide[-1]["end_time"] = "2026-08-15T20:00:01+00:00"
    with pytest.raises(CloseoutError, match="3600"):
        validate_span(wide)
    future = deepcopy(records)
    future[-1]["end_time"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(CloseoutError, match="3600|future"):
        validate_span(future)


def test_write_restore_and_reuse_and_nonempty_output(tmp_path):
    from build_tools.plan005_closeout_control import (
        CloseoutError,
        refuse_nonempty_control_output,
        validate_protocol_binding,
    )

    evidence = tmp_path / "abc123"
    evidence.mkdir()
    records = _synthetic_records(evidence, "/home/taishajo/work/NExtSEEK-plan005", "abc123", "t")
    restored = deepcopy(records)
    restored[3]["evidence_root_after"] = {}
    with pytest.raises(CloseoutError, match="write\\+restore|undeclared|removed"):
        validate_protocol_binding(
            restored,
            repo="/home/taishajo/work/NExtSEEK-plan005",
            evidence_root=evidence,
            candidate="abc123",
        )
    mutated = deepcopy(records)
    mutated[2]["evidence_root_after"]["records/01-baseline/record.json"] = "f" * 64
    with pytest.raises(CloseoutError, match="mutated"):
        validate_protocol_binding(
            mutated,
            repo="/home/taishajo/work/NExtSEEK-plan005",
            evidence_root=evidence,
            candidate="abc123",
        )
    out = tmp_path / "control" / "preflight"
    out.mkdir(parents=True)
    refuse_nonempty_control_output(out, "pre-cold-closeout.json")
    (out / "stale.txt").write_text("x")
    with pytest.raises(CloseoutError, match="nonempty"):
        refuse_nonempty_control_output(out, "pre-cold-closeout.json")


def test_signoff_and_plan_copy_and_head_swap_mutations(tmp_path):
    from build_tools.plan005_closeout_control import (
        CloseoutError,
        presented_diff_hash,
        validate_protocol_binding,
        validate_signoffs,
        validate_vet_reports,
    )

    repo = tmp_path / "repo"
    (repo / "dmac_assistant/build_context").mkdir(parents=True)
    artifact = repo / "dmac_assistant/build_context/route_capabilities.json"
    artifact.write_bytes(b'{"ok": true}\n')
    signoff_dir = tmp_path / "signoffs"
    signoff_dir.mkdir()
    rel = "dmac_assistant/build_context/route_capabilities.json"
    digest = __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
    from build_tools.plan005_closeout import ROUTE_CAPABILITIES_SHA

    # Force STOP on hash change vs locked route SHA.
    with pytest.raises(CloseoutError, match="STOP"):
        validate_signoffs(repo, signoff_dir)

    real = Path(__file__).resolve().parents[2]
    artifact.write_bytes(
        (real / "dmac_assistant/build_context/route_capabilities.json").read_bytes()
    )
    copied = tmp_path / "signoffs2"
    copied.mkdir()
    for src in (real / "build_tools/plan005_signoffs").glob("*.json"):
        shutil.copy2(src, copied / src.name)
    validate_signoffs(real, copied)

    payload = json.loads((copied / "task-11-route-capabilities.json").read_text())
    payload["interpretation_source"] = "agent_inferred"
    (copied / "task-11-route-capabilities.json").write_text(json.dumps(payload))
    with pytest.raises(CloseoutError, match="user_stated"):
        validate_signoffs(real, copied)

    reports = [
        tmp_path / "a.md",
        tmp_path / "b.md",
        tmp_path / "c.md",
    ]
    for path in reports:
        path.write_text("SHA f9574f25 PASS\n")
    validate_vet_reports(reports, "f9574f25a0aa3f04f837e3bc1b653b50d031fa50eefd8a15228b8dc885b23fb8")
    reports[0].write_text("wrong sha BLOCK\n")
    with pytest.raises(CloseoutError, match="approved plan SHA"):
        validate_vet_reports(reports, "f9574f25a0aa3f04f837e3bc1b653b50d031fa50eefd8a15228b8dc885b23fb8")

    evidence_a = tmp_path / ("a" * 40)
    evidence_b = tmp_path / ("b" * 40)
    evidence_a.mkdir()
    evidence_b.mkdir()
    rec_a = _synthetic_records(evidence_a, "/home/taishajo/work/NExtSEEK-plan005", "a" * 40, "ta")
    rec_b = _synthetic_records(evidence_b, "/home/taishajo/work/NExtSEEK-plan005", "b" * 40, "tb")
    swapped = deepcopy(rec_a)
    swapped[0]["pre"]["head"] = "b" * 40
    with pytest.raises(CloseoutError, match="argv|HEAD"):
        validate_protocol_binding(
            swapped,
            repo="/home/taishajo/work/NExtSEEK-plan005",
            evidence_root=evidence_a,
            candidate="a" * 40,
        )
    assert rec_b[0]["pre"]["head"] != rec_a[0]["pre"]["head"]
    assert presented_diff_hash(real, [rel]).startswith("fc59")


def test_finalize_requires_cold_pass(tmp_path):
    from build_tools.plan005_closeout_control import CloseoutError, _require_cold_pass

    path = tmp_path / "plan005-cold-outcome-review.md"
    path.write_text("implementer PASS\n")
    with pytest.raises(CloseoutError, match="provenance"):
        _require_cold_pass(path)
    path.write_text(
        "reviewer_kind: cold_subagent\nsubagent_id: x\nprompt_verbatim: true\nverdict: PARTIAL\n"
    )
    with pytest.raises(CloseoutError, match="not PASS"):
        _require_cold_pass(path)
    path.write_text(
        "reviewer_kind: cold_subagent\nsubagent_id: x\nprompt_verbatim: true\nA bare PASS token is not enough.\n"
    )
    with pytest.raises(CloseoutError, match="not PASS"):
        _require_cold_pass(path)
    path.write_text(
        "reviewer_kind: cold_subagent\nsubagent_id: x\nprompt_verbatim: true\nverdict: PASS\n"
    )
    _require_cold_pass(path)


def test_named_closeout_mutants_plan_baml_stale_signoff_and_producer(tmp_path):
    from build_tools.plan005_closeout import APPROVED_PLAN_SHA, PLAN_REL
    from build_tools.plan005_closeout_control import (
        CloseoutError,
        assert_baml_hash_equality,
        rehash_bound_evidence,
        validate_plan_copies,
        validate_producer_consumer,
        validate_record_commit_times,
        validate_signoffs,
    )

    src = {"classifier.baml": "a" * 64}
    client = {"__init__.py": "b" * 64}
    assert_baml_hash_equality(
        current_src=src,
        current_client=client,
        baseline_src=dict(src),
        baseline_client=dict(client),
        setup_client={"dmac_assistant/src/dmac_assistant/router/baml_client/__init__.py": "b" * 64},
    )
    drifted = dict(client)
    drifted["__init__.py"] = "c" * 64
    with pytest.raises(CloseoutError, match="STOP"):
        assert_baml_hash_equality(
            current_src=src,
            current_client=drifted,
            baseline_src=dict(src),
            baseline_client=dict(client),
            setup_client={"dmac_assistant/src/dmac_assistant/router/baml_client/__init__.py": "b" * 64},
        )

    plan = tmp_path / "plan.md"
    mirror = tmp_path / "mirror.md"
    plan.write_bytes(b"alpha")
    mirror.write_bytes(b"alpha")
    with pytest.raises(CloseoutError, match="approved"):
        validate_plan_copies(plan, mirror, APPROVED_PLAN_SHA)
    real_plan = Path("/home/taishajo/work/NExtSEEK-plan005") / PLAN_REL
    if real_plan.is_file():
        plan.write_bytes(real_plan.read_bytes())
        mirror.write_bytes(real_plan.read_bytes() + b"x")
        with pytest.raises(CloseoutError, match="unequal plan copies"):
            validate_plan_copies(plan, mirror, APPROVED_PLAN_SHA)

    records = _synthetic_records(tmp_path / "abc123", "/repo", "abc123", "tree1")
    (tmp_path / "abc123").mkdir(exist_ok=True)
    with pytest.raises(CloseoutError, match="stale timestamp"):
        validate_record_commit_times(
            records,
            identity={"head": "abc123", "tree": "tree1"},
            commit_time="2026-08-15T19:00:00+00:00",
        )

    real = Path("/home/taishajo/work/NExtSEEK-plan005")
    clone = tmp_path / "signed-repo"
    for rel in (
        "dmac_assistant/build_context/route_capabilities.json",
        "docker/cc-runtime/build_context/plugins/nextseek/.claude-plugin/plugin.json",
        "docker/cc-runtime/build_context/plugins/nextseek/commands/nextseek.md",
        "docker/cc-runtime/build_context/plugins/nextseek/skills/nextseek/SKILL.md",
        "docker/cc-runtime/build_context/plugins/nextseek/skills/nextseek-batch-upload/SKILL.md",
        "docker/cc-runtime/Dockerfile",
        "docker-compose.yml",
        "docker/cc-runtime/container/CLAUDE.md",
    ):
        dest = clone / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((real / rel).read_bytes())
    signoff_dir = tmp_path / "signoffs-copy"
    signoff_dir.mkdir()
    for src_path in (real / "build_tools/plan005_signoffs").glob("*.json"):
        shutil.copy2(src_path, signoff_dir / src_path.name)
    validate_signoffs(clone, signoff_dir)
    (clone / "docker-compose.yml").write_bytes(
        (clone / "docker-compose.yml").read_bytes() + b"\n"
    )
    with pytest.raises(CloseoutError, match="earlier bytes"):
        validate_signoffs(clone, signoff_dir)

    evidence = tmp_path / "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    evidence.mkdir()
    recs = _synthetic_records(
        evidence, "/home/taishajo/work/NExtSEEK-plan005", evidence.name, "tree"
    )
    with pytest.raises(CloseoutError, match="missing"):
        validate_producer_consumer(recs, evidence)

    bound = {"evidence:records/missing.json": "0" * 64}
    with pytest.raises((CloseoutError, FileNotFoundError)):
        rehash_bound_evidence(
            bound,
            repo_root=real,
            evidence_root=evidence,
        )
