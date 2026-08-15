"""Hermetic tests for
nextseek_api/cc_assistant/scripts/verify_prod_readiness_manifest.py (Task 13).

No docker, no live E2E run, no real git repo dependency for the assertions
below (git IS available in this dev environment, but every test explicitly
injects a fake `git_rev_parse_head`/`docker_image_inspect`/
`run_full_ui_e2e_validate`/`run_survivals` rather than relying on ambient
state, so the suite is deterministic and reproducible with none of docker/
git/a real E2E harness present).

Run exactly as specified for this task:

    cd /home/taishajo/work/NExtSEEK-merge && \\
      uv run --no-project --with pytest python -m pytest -q --noconftest \\
      nextseek_api/cc_assistant/tests/test_verify_prod_readiness_manifest.py

The module under test has zero non-stdlib imports, so this file only needs
pytest itself. It is loaded via `importlib.util.spec_from_file_location`
(matching test_full_ui_e2e.py's convention) since `scripts/` is not an
installed/importable package.

Two ways of injecting the 4 "reach outside this process" primitives are
exercised on purpose (the module supports both):
  1. Passing fakes directly as keyword arguments to `verify()` — used by
     most tests below (`test_positive_all_present_valid_manifest_passes` and
     the negative tests derived from its baseline).
  2. `monkeypatch.setattr(module, "_the_function", fake)` — used by
     `test_module_level_monkeypatch_of_all_four_primitives_is_honored` and
     `test_cli_main_...`, proving the bare-module-global lookup inside
     verify()'s helpers really is monkeypatchable (not captured as a
     default-parameter value at import time).
  3. A REAL subprocess call to a tiny stub script on disk — used by
     `test_e2e_never_trusts_summary_json_even_after_mutation`, the flagship
     proof that "never trust summary.json" is really wired through a
     subprocess exit code and not a shortcut read of the JSON.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "verify_prod_readiness_manifest.py"
)
_spec = importlib.util.spec_from_file_location(
    "nextseek_api.cc_assistant.scripts.verify_prod_readiness_manifest", _SCRIPT_PATH
)
vprm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vprm
sys.modules["verify_prod_readiness_manifest"] = vprm
_spec.loader.exec_module(vprm)


MERGED_SHA = "a" * 40
OTHER_SHA = "f" * 40
PARENT_SHAS = ["b" * 40, "c" * 40]
BLOCKER_SHAS = ["1234567", "89abcde"]
IMAGE_MAP = {
    "nextseek:devmerge": "sha256:" + "1" * 64,
    "cc-runtime:devmerge": "sha256:" + "2" * 64,
}


# ── Fake injectables (baseline: everything "passes") ───────────────────────


def _ok_git(repo_root):
    return MERGED_SHA


def _ok_docker(tag):
    return IMAGE_MAP.get(tag)


def _ok_e2e_validate(script, approval_path, run_dir):
    return SimpleNamespace(returncode=0, stdout="VALIDATE: PASS", stderr="")


def _ok_survivals(script):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _default_runners() -> dict:
    return dict(
        git_rev_parse_head=_ok_git,
        docker_image_inspect=_ok_docker,
        run_full_ui_e2e_validate=_ok_e2e_validate,
        run_survivals=_ok_survivals,
    )


# ── Synthetic evidence-tree builder ────────────────────────────────────────


def _write_json(path: Path, content) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _wrap(path: Path, *, command=("fake", "cmd"), exit_code=0, candidate_sha=MERGED_SHA, **extra) -> dict:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "command": list(command),
        "exit_code": exit_code,
        "candidate_sha": candidate_sha,
        **extra,
    }


def _wrap_dir(dir_path: Path, *, command=("fake", "cmd"), exit_code=0, candidate_sha=MERGED_SHA, **extra) -> dict:
    return {
        "path": str(dir_path),
        "sha256": hashlib.sha256((dir_path / "summary.json").read_bytes()).hexdigest(),
        "command": list(command),
        "exit_code": exit_code,
        "candidate_sha": candidate_sha,
        **extra,
    }


def build_valid_manifest(tmp_path: Path) -> tuple[dict, dict]:
    """Builds a full synthetic evidence tree under tmp_path and a manifest
    dict that (given the `_ok_*` injectables) verifies clean. Returns
    (manifest, paths) — `paths` exposes every raw artifact path so negative
    tests can mutate exactly one thing."""
    ev = tmp_path / "evidence"

    image_provenance_path = _write_json(ev / "image_provenance.json", {
        "images": [
            {"tag": "nextseek:devmerge", "image_id": IMAGE_MAP["nextseek:devmerge"]},
            {"tag": "cc-runtime:devmerge", "image_id": IMAGE_MAP["cc-runtime:devmerge"]},
        ],
    })

    lane_a_path = _write_json(ev / "lane_a.json", {
        "collected": ["nextseek_api/cc_assistant/tests/test_x.py::test_a",
                      "nextseek_api/cc_assistant/tests/test_x.py::test_b"],
        "failures": [],
        "skips": [{"nodeid": "nextseek_api/cc_assistant/tests/test_x.py::test_c",
                   "reason": "requires GPU", "expected": True}],
        "xfails": [],
        "deselected": [],
        "image_id": IMAGE_MAP["nextseek:devmerge"],
        "source_sha": MERGED_SHA,
        "db_identity": {"env": "dev", "host": "db", "port": 3306, "user": "seek_db_user"},
    })
    lane_b_path = _write_json(ev / "lane_b.json", {
        "collected": ["nextseek_api/cc_assistant/tests/test_hygiene.py::test_gitignore"],
        "failures": [], "skips": [], "xfails": [], "deselected": [],
        "image_id": None, "source_sha": MERGED_SHA, "db_identity": None,
    })

    secret_scan_path = _write_json(ev / "secret_scan_nextseek.json", {
        "image_tag": "nextseek:devmerge",
        "image_id": IMAGE_MAP["nextseek:devmerge"],
        "export_member_count": 4321,
        "scanned_bytes": 987654321,
        "command": ["scan-secrets", "nextseek:devmerge"],
        "exit_code": 0,
        "categories": {
            "filename": {
                "hits": [{"match": "AKIA_FAKE_TEST_TOKEN_1234", "location": "app/fixtures/example.env"}],
                "allowlist": [{"match": "AKIA_FAKE_TEST_TOKEN_1234",
                               "reason": "synthetic test fixture, not a real credential"}],
            },
            "value": {"hits": [], "allowlist": []},
            "key_entropy": {"hits": [], "allowlist": []},
            "config_env": {"hits": [], "allowlist": []},
        },
    })

    migration_evidence_path = _write_json(ev / "migration_evidence.json", {
        "required_tables": ["assistant_chat_session", "assistant_cc_transcript", "django_migrations"],
        "present_tables": ["assistant_chat_session", "assistant_cc_transcript", "django_migrations", "seek_users"],
        "migration_0007_present": True,
        "foreign_keys": [{"table": "assistant_cc_transcript",
                          "constraint": "fk_transcript_session", "present": True}],
        "charset_equality": [{"child_table": "assistant_cc_transcript", "parent_table": "assistant_chat_session",
                              "child_charset": "utf8mb4", "parent_charset": "utf8mb4"}],
        "second_migrate_output": "Operations to perform:\n  Apply all migrations\nRunning migrations:\n  No migrations to apply.",
        "error_log_excerpt": "clean run, no MySQL errors observed",
    })

    db_backup_path = _write_text(ev / "db_backup.sql", "-- mysqldump fake fixture\nINSERT INTO x VALUES (1);\n")
    ledger_path = _write_text(ev / "ledger.md", "| conflict | resolution |\n|---|---|\n| entrypoint.sh | union |\n")
    host_only_path = _write_text(ev / "host_only_allowlist_output.txt", "PASS: host_only markers and allowlist agree exactly.\n")
    survivals_output_path = _write_text(ev / "survivals_output.txt", "PASS S1\nPASS S2\n...\n")

    approval_path = _write_json(ev / "approval.json", {
        "base_url": "http://localhost:9001", "max_total_usd": 1.0,
        "forbidden_phrases": ["backend is unreachable"], "questions": [],
    })
    run_dir = ev / "e2e_run"
    _write_json(run_dir / "summary.json", {"all_passed": True, "total_cost_usd": 0.01, "results": []})

    manifest = {
        "merged_sha": MERGED_SHA,
        "merge_parents": PARENT_SHAS,
        "image_ids": {im["tag"]: im["image_id"] for im in json.loads(image_provenance_path.read_text())["images"]},
        "image_provenance": _wrap(image_provenance_path, command=["docker", "image", "inspect", "..."]),
        "lanes_artifacts": [
            _wrap(lane_a_path, command=["pytest", "-m", "not host_only"]),
            _wrap(lane_b_path, command=["pytest", "checkout-hygiene"]),
        ],
        "migration_evidence": _wrap(migration_evidence_path, command=["manage.py", "migrate", "--noinput"]),
        "e2e_run_dir": _wrap_dir(run_dir, command=["full_ui_e2e.py", "--approval", "...", "--run-dir", "..."],
                                 approval_path=str(approval_path),
                                 approval_sha256=hashlib.sha256(approval_path.read_bytes()).hexdigest()),
        "e2e_approval_sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
        "db_backup": _wrap(db_backup_path, command=["mysqldump", "dmac"], container_id="db-container-abc123"),
        "db_backup_sha256": hashlib.sha256(db_backup_path.read_bytes()).hexdigest(),
        "ledger": _wrap(ledger_path, command=["write-ledger"]),
        "survivals_output": _wrap(survivals_output_path, command=["verify_merge_survivals.py"]),
        "host_only_allowlist_output": _wrap(host_only_path, command=["verify_host_only_allowlist.py"]),
        "secret_scan_artifacts": [_wrap(secret_scan_path, command=["scan-secrets", "nextseek:devmerge"])],
        "blocker_fix_shas": BLOCKER_SHAS,
    }
    paths = dict(
        image_provenance=image_provenance_path, lane_a=lane_a_path, lane_b=lane_b_path,
        secret_scan=secret_scan_path, migration_evidence=migration_evidence_path,
        db_backup=db_backup_path, ledger=ledger_path, host_only=host_only_path,
        survivals_output=survivals_output_path, approval=approval_path, run_dir=run_dir,
    )
    return manifest, paths


def write_manifest(tmp_path: Path, manifest: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return p


def rewrap(paths_entry: Path, wrapped: dict, **overrides) -> dict:
    """Re-hash `paths_entry` (after mutating its bytes on disk) into a copy
    of `wrapped`, applying any additional field overrides."""
    new = dict(wrapped)
    new["sha256"] = hashlib.sha256(paths_entry.read_bytes()).hexdigest()
    new.update(overrides)
    return new


# ── Positive control ────────────────────────────────────────────────────────


def test_positive_all_present_valid_manifest_passes(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is True, errs
    assert errs == []


# ── Missing field ───────────────────────────────────────────────────────────


def test_missing_top_level_field_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    del manifest["ledger"]
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("missing field: ledger" in e for e in errs)


# ── Stale merged_sha ─────────────────────────────────────────────────────────


def test_stale_merged_sha_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()
    runners["git_rev_parse_head"] = lambda repo_root: OTHER_SHA
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("merged_sha mismatch" in e for e in errs)


# ── Image provenance / tag->ID mapping ──────────────────────────────────────


def test_wrong_image_id_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()
    runners["docker_image_inspect"] = lambda tag: "sha256:" + "9" * 64 if tag == "nextseek:devmerge" else IMAGE_MAP.get(tag)
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("image provenance drift" in e and "nextseek:devmerge" in e for e in errs)


def test_changed_tag_to_id_mapping_only_one_tag_drifts(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()

    def drifted(tag):
        if tag == "cc-runtime:devmerge":
            return "sha256:" + "deadbeef" * 8
        return IMAGE_MAP.get(tag)

    runners["docker_image_inspect"] = drifted
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("cc-runtime:devmerge" in e and "drift" in e for e in errs)
    assert not any("nextseek:devmerge" in e and "drift" in e for e in errs)


def test_image_ids_top_level_disagrees_with_provenance_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    manifest["image_ids"]["nextseek:devmerge"] = "sha256:" + "0" * 64
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("image_ids top-level dict disagrees" in e for e in errs)


def test_docker_inspect_tag_no_longer_resolves_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()
    runners["docker_image_inspect"] = lambda tag: None
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("no longer resolves" in e for e in errs)


# ── Lane artifacts ───────────────────────────────────────────────────────────


def test_failing_lane_json_reports_failures(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["lane_a"].read_text())
    content["failures"] = ["nextseek_api/cc_assistant/tests/test_x.py::test_a"]
    paths["lane_a"].write_text(json.dumps(content))
    manifest["lanes_artifacts"][0] = rewrap(paths["lane_a"], manifest["lanes_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("failing node" in e for e in errs)


def test_unexpected_skip_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["lane_a"].read_text())
    content["skips"][0]["expected"] = False
    paths["lane_a"].write_text(json.dumps(content))
    manifest["lanes_artifacts"][0] = rewrap(paths["lane_a"], manifest["lanes_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("unexpected skips" in e for e in errs)


def test_unexpected_deselect_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["lane_a"].read_text())
    content["deselected"] = [{"nodeid": "nextseek_api/cc_assistant/tests/test_x.py::test_d",
                              "reason": "flaky"}]  # no "expected": true
    paths["lane_a"].write_text(json.dumps(content))
    manifest["lanes_artifacts"][0] = rewrap(paths["lane_a"], manifest["lanes_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("unexpected deselected" in e for e in errs)


def test_unexpected_xfail_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["lane_a"].read_text())
    content["xfails"] = [{"nodeid": "nextseek_api/cc_assistant/tests/test_x.py::test_e", "reason": "flaky"}]
    paths["lane_a"].write_text(json.dumps(content))
    manifest["lanes_artifacts"][0] = rewrap(paths["lane_a"], manifest["lanes_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("unexpected xfails" in e for e in errs)


def test_lane_placeholder_file_not_json_object_rejected(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    paths["lane_b"].write_text("TODO: fill in real lane evidence later\n")
    manifest["lanes_artifacts"][1] = rewrap(paths["lane_b"], manifest["lanes_artifacts"][1])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("unreadable JSON" in e for e in errs)


def test_lane_missing_content_keys_rejected(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["lane_a"].read_text())
    del content["db_identity"]
    paths["lane_a"].write_text(json.dumps(content))
    manifest["lanes_artifacts"][0] = rewrap(paths["lane_a"], manifest["lanes_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("content missing keys" in e and "db_identity" in e for e in errs)


# ── Secret scan ──────────────────────────────────────────────────────────────


def test_missing_secret_scan_category_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["secret_scan"].read_text())
    del content["categories"]["config_env"]
    paths["secret_scan"].write_text(json.dumps(content))
    manifest["secret_scan_artifacts"][0] = rewrap(paths["secret_scan"], manifest["secret_scan_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("missing secret-scan categories" in e and "config_env" in e for e in errs)


def test_missing_secret_scan_image_id_key_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["secret_scan"].read_text())
    del content["image_id"]
    paths["secret_scan"].write_text(json.dumps(content))
    manifest["secret_scan_artifacts"][0] = rewrap(paths["secret_scan"], manifest["secret_scan_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("content missing keys" in e and "image_id" in e for e in errs)


def test_broad_exclusion_secret_scan_allowlist_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["secret_scan"].read_text())
    content["categories"]["value"] = {
        "hits": [{"match": "AKIA_REAL_LOOKING_SECRET_9999", "location": "app/settings.py"}],
        "allowlist": [{"match": "*", "reason": "allowlist everything (bypass)"}],
    }
    paths["secret_scan"].write_text(json.dumps(content))
    manifest["secret_scan_artifacts"][0] = rewrap(paths["secret_scan"], manifest["secret_scan_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("overly broad" in e for e in errs)


def test_unallowlisted_secret_hit_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["secret_scan"].read_text())
    content["categories"]["key_entropy"] = {
        "hits": [{"match": "high-entropy-blob-abcdefg123456", "location": "app/config.py"}],
        "allowlist": [],
    }
    paths["secret_scan"].write_text(json.dumps(content))
    manifest["secret_scan_artifacts"][0] = rewrap(paths["secret_scan"], manifest["secret_scan_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("un-allowlisted secret hit" in e for e in errs)


def test_secret_scan_placeholder_file_rejected(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    paths["secret_scan"].write_text("scan pending\n")
    manifest["secret_scan_artifacts"][0] = rewrap(paths["secret_scan"], manifest["secret_scan_artifacts"][0])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("unreadable JSON" in e for e in errs)


# ── Migration evidence ───────────────────────────────────────────────────────


def test_migration_evidence_missing_fk_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["migration_evidence"].read_text())
    content["foreign_keys"] = []
    paths["migration_evidence"].write_text(json.dumps(content))
    manifest["migration_evidence"] = rewrap(paths["migration_evidence"], manifest["migration_evidence"])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("no foreign_keys recorded" in e for e in errs)


def test_migration_evidence_charset_mismatch_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["migration_evidence"].read_text())
    content["charset_equality"][0]["child_charset"] = "latin1"
    paths["migration_evidence"].write_text(json.dumps(content))
    manifest["migration_evidence"] = rewrap(paths["migration_evidence"], manifest["migration_evidence"])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("charset mismatch" in e for e in errs)


def test_migration_evidence_not_idempotent_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["migration_evidence"].read_text())
    content["second_migrate_output"] = "Applying assistant.0009_something... OK\n"
    paths["migration_evidence"].write_text(json.dumps(content))
    manifest["migration_evidence"] = rewrap(paths["migration_evidence"], manifest["migration_evidence"])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("not confirmed idempotent" in e for e in errs)


def test_migration_evidence_0007_missing_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    content = json.loads(paths["migration_evidence"].read_text())
    content["migration_0007_present"] = False
    paths["migration_evidence"].write_text(json.dumps(content))
    manifest["migration_evidence"] = rewrap(paths["migration_evidence"], manifest["migration_evidence"])
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("migration 0007 row not confirmed" in e for e in errs)


# ── DB backup ────────────────────────────────────────────────────────────────


def test_db_backup_checksum_mismatch_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    # Mutate the dump AFTER wrapping, so the manifest's recorded sha256 is stale.
    with open(paths["db_backup"], "a", encoding="utf-8") as fh:
        fh.write("-- tampered row appended after backup was recorded\n")
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("db_backup: sha256 mismatch" in e for e in errs)


def test_db_backup_sha256_top_level_drift_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    manifest["db_backup_sha256"] = "0" * 64
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("db_backup_sha256: top-level value disagrees" in e for e in errs)


# ── Approval hash drift ──────────────────────────────────────────────────────


def test_approval_hash_drift_fails(tmp_path):
    manifest, paths = build_valid_manifest(tmp_path)
    # Approval file mutated after the manifest recorded its hash (simulates
    # someone editing prompts/criteria post-approval).
    content = json.loads(paths["approval"].read_text())
    content["max_total_usd"] = 999.0
    paths["approval"].write_text(json.dumps(content))
    mpath = write_manifest(tmp_path, manifest)
    ok, errs = vprm.verify(mpath, **_default_runners())
    assert ok is False
    assert any("approval hash drift" in e for e in errs)


# ── E2E validate-only wiring ─────────────────────────────────────────────────


def test_e2e_validate_only_nonzero_exit_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()
    runners["run_full_ui_e2e_validate"] = lambda script, approval, run_dir: SimpleNamespace(
        returncode=1, stdout="", stderr="VALIDATE: FAIL: forbidden phrase detected"
    )
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("full_ui_e2e --validate-only failed" in e for e in errs)


def test_e2e_never_trusts_summary_json_even_after_mutation(tmp_path):
    """Flagship test: `summary.json` says all_passed=true throughout, but a
    REAL subprocess stub (recomputing a hash from run-dir artifacts, exactly
    the shape the real full_ui_e2e.py --validate-only uses) independently
    detects a post-hoc mutation of a raw artifact and exits 1 — proving
    `verify()`'s e2e gate is wired to the subprocess exit code, not to
    `summary.json`'s own claim (which a naive implementation could have
    trusted and wrongly passed)."""
    manifest, paths = build_valid_manifest(tmp_path)
    run_dir = paths["run_dir"]

    # A raw "artifact" the real E2E run would have produced (e.g. a turn's
    # transcript), plus a manifest.json recording its hash at run time —
    # mirrors full_ui_e2e.py's own _artifact_manifest()/_write_manifest().
    artifact_path = run_dir / "artifact.txt"
    artifact_path.write_text("original assistant reply, no forbidden phrases", encoding="utf-8")
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"artifact.txt": hashlib.sha256(artifact_path.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    # summary.json still claims success (this is the "still-passing" part).
    (run_dir / "summary.json").write_text(json.dumps({"all_passed": True}), encoding="utf-8")
    manifest["e2e_run_dir"] = rewrap(run_dir / "summary.json", manifest["e2e_run_dir"])

    stub = tmp_path / "fake_full_ui_e2e.py"
    stub.write_text(
        "import hashlib, json, sys\n"
        "from pathlib import Path\n"
        "run_dir = Path(sys.argv[sys.argv.index('--run-dir') + 1])\n"
        "recorded = json.loads((run_dir / 'run_manifest.json').read_text())\n"
        "ok = all(\n"
        "    (run_dir / name).is_file()\n"
        "    and hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == digest\n"
        "    for name, digest in recorded.items()\n"
        ")\n"
        "print('VALIDATE:', 'PASS' if ok else 'FAIL')\n"
        "sys.exit(0 if ok else 1)\n",
        encoding="utf-8",
    )
    mpath = write_manifest(tmp_path, manifest)

    runners = _default_runners()
    del runners["run_full_ui_e2e_validate"]  # use the REAL subprocess wrapper this time

    # Baseline (unmutated): the real subprocess call must pass.
    ok, errs = vprm.verify(mpath, full_ui_e2e_script=stub, **runners)
    assert ok is True, errs

    # Mutate the raw artifact AFTER the "run" — summary.json is untouched and
    # still says all_passed=true.
    artifact_path.write_text("MUTATED reply text after the run completed", encoding="utf-8")
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["all_passed"] is True  # sanity: summary.json still claims success

    ok, errs = vprm.verify(mpath, full_ui_e2e_script=stub, **runners)
    assert ok is False
    assert any("full_ui_e2e --validate-only failed" in e for e in errs)


# ── Survivals re-run wiring ──────────────────────────────────────────────────


def test_survivals_rerun_nonzero_fails(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)
    runners = _default_runners()
    runners["run_survivals"] = lambda script: SimpleNamespace(returncode=1, stdout="FAIL S7", stderr="")
    ok, errs = vprm.verify(mpath, **runners)
    assert ok is False
    assert any("survival assertions fail" in e for e in errs)


def test_module_level_monkeypatch_of_all_four_primitives_is_honored(tmp_path, monkeypatch):
    """Proves the OTHER injection mechanism the brief allows: monkeypatching
    the module-level functions directly (rather than passing kwargs to
    verify()) is honored, because verify()'s helpers call them by their bare
    global name."""
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)

    monkeypatch.setattr(vprm, "_git_rev_parse_head", _ok_git)
    monkeypatch.setattr(vprm, "_docker_image_inspect", _ok_docker)
    monkeypatch.setattr(vprm, "_run_full_ui_e2e_validate", _ok_e2e_validate)
    monkeypatch.setattr(vprm, "_run_survivals", _ok_survivals)

    ok, errs = vprm.verify(mpath)  # no kwargs — must fall back to the module globals above
    assert ok is True, errs

    monkeypatch.setattr(vprm, "_run_survivals", lambda script: SimpleNamespace(returncode=1))
    ok, errs = vprm.verify(mpath)
    assert ok is False
    assert any("survival assertions fail" in e for e in errs)


# ── CLI-level coverage ───────────────────────────────────────────────────────


def test_cli_main_verify_exit_codes(tmp_path, monkeypatch, capsys):
    manifest, _ = build_valid_manifest(tmp_path)
    mpath = write_manifest(tmp_path, manifest)

    monkeypatch.setattr(vprm, "_git_rev_parse_head", _ok_git)
    monkeypatch.setattr(vprm, "_docker_image_inspect", _ok_docker)
    monkeypatch.setattr(vprm, "_run_full_ui_e2e_validate", _ok_e2e_validate)
    monkeypatch.setattr(vprm, "_run_survivals", _ok_survivals)

    rc = vprm.main([str(mpath), "--verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "MANIFEST: PASS" in out

    del manifest["ledger"]
    mpath2 = write_manifest(tmp_path, manifest)
    rc2 = vprm.main([str(mpath2), "--verify"])
    out2 = capsys.readouterr().out
    assert rc2 == 1
    assert "MANIFEST: FAIL" in out2
    assert "FAIL: missing field: ledger" in out2


def test_generate_writes_manifest_with_schema_version(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    out = tmp_path / "generated.json"
    vprm.generate(out, manifest)
    written = json.loads(out.read_text())
    assert written["schema_version"] == vprm.SCHEMA_VERSION
    assert written["merged_sha"] == MERGED_SHA
    # generate() does not itself validate — round-tripping through verify()
    # with the same fakes must still pass, proving generate+verify compose.
    ok, errs = vprm.verify(out, **_default_runners())
    assert ok is True, errs


def test_cli_main_generate_placeholder_message_when_no_from(tmp_path, capsys):
    out = tmp_path / "generated.json"
    rc = vprm.main([str(out)])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "use Task 17 to generate a real manifest" in captured
    assert not out.exists()


def test_cli_main_generate_with_from_json(tmp_path):
    manifest, _ = build_valid_manifest(tmp_path)
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "generated2.json"
    rc = vprm.main([str(out), "--from", str(fields_path)])
    assert rc == 0
    written = json.loads(out.read_text())
    assert written["merged_sha"] == MERGED_SHA


def test_subprocess_wrappers_and_artifact_helpers(tmp_path, monkeypatch):
    def git_ok(cmd, **kw):
        return SimpleNamespace(stdout="abc\n", returncode=0, stderr="")

    monkeypatch.setattr(vprm.subprocess, "run", git_ok)
    assert vprm._git_rev_parse_head(tmp_path) == "abc"

    monkeypatch.setattr(
        vprm.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="sha256:img\n", returncode=0, stderr=""),
    )
    assert vprm._docker_image_inspect("tag") == "sha256:img"
    monkeypatch.setattr(
        vprm.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="", returncode=1, stderr="missing"),
    )
    assert vprm._docker_image_inspect("tag") is None
    monkeypatch.setattr(
        vprm.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="   ", returncode=0, stderr=""),
    )
    assert vprm._docker_image_inspect("tag") is None

    captured = {}

    def capture(cmd, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vprm.subprocess, "run", capture)
    script = tmp_path / "s.py"
    script.write_text("x")
    vprm._run_full_ui_e2e_validate(script, tmp_path / "a.json", tmp_path / "run")
    assert "--validate-only" in captured["cmd"]
    vprm._run_survivals(script)
    assert captured["cmd"][-1] == str(script)

    ok, errs = vprm.verify(tmp_path / "missing.json", **_default_runners())
    assert ok is False
    assert any("unreadable" in e for e in errs)
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    ok, errs = vprm.verify(bad, **_default_runners())
    assert any("not valid JSON" in e for e in errs)
    not_obj = tmp_path / "arr.json"
    not_obj.write_text("[]")
    ok, errs = vprm.verify(not_obj, **_default_runners())
    assert any("not a JSON object" in e for e in errs)

    errs = []
    assert vprm._check_file_artifact(errs, "ledger", "nope", MERGED_SHA) is None
    assert vprm._check_file_artifact(errs, "ledger", {}, MERGED_SHA) is None
    rec = {"path": str(tmp_path / "no-file"), "sha256": "x", "command": [], "exit_code": 1,
           "candidate_sha": OTHER_SHA}
    assert vprm._check_file_artifact(errs, "ledger", rec, MERGED_SHA) is None
    art = tmp_path / "art.bin"
    art.write_bytes(b"abc")
    rec = {
        "path": str(art), "sha256": "dead", "command": "not-a-list", "exit_code": 2,
        "candidate_sha": OTHER_SHA,
    }
    p = vprm._check_file_artifact(errs, "ledger", rec, MERGED_SHA)
    assert p == art
    assert any("sha256 mismatch" in e for e in errs)

    e2e_errs = []
    assert vprm._check_e2e_run_dir(e2e_errs, "nope", MERGED_SHA) is None
    assert vprm._check_e2e_run_dir(e2e_errs, {}, MERGED_SHA) is None
    assert vprm._check_e2e_run_dir(e2e_errs, {"path": str(tmp_path / "missing-dir")}, MERGED_SHA) is None
    rundir = tmp_path / "e2e-run"
    rundir.mkdir()
    rec = {
        "path": str(rundir), "sha256": "x", "command": ["e2e"], "exit_code": 1,
        "candidate_sha": OTHER_SHA, "approval_path": "a", "approval_sha256": "b",
    }
    assert vprm._check_e2e_run_dir(e2e_errs, rec, MERGED_SHA) is None
    (rundir / "summary.json").write_text("{}")
    rec["sha256"] = "dead"
    rec["exit_code"] = 0
    rec["candidate_sha"] = MERGED_SHA
    assert vprm._check_e2e_run_dir(e2e_errs, rec, MERGED_SHA) is not None

    assert vprm._known_image_ids("x") == set()
    assert vprm._known_image_ids({"no": "path"}) == set()
    assert vprm._known_image_ids({"path": str(tmp_path / "missing.json")}) == set()
    junk = tmp_path / "prov.json"
    junk.write_text("{")
    assert vprm._known_image_ids({"path": str(junk)}) == set()
    junk.write_text(json.dumps({"images": "nope"}))
    assert vprm._known_image_ids({"path": str(junk)}) == set()
    junk.write_text(json.dumps({"images": [{"image_id": "i1"}, "x"]}))
    assert vprm._known_image_ids({"path": str(junk)}) == {"i1"}

    lane_errs = []
    vprm._check_lane_entries(lane_errs, "lane", "skips", "nope")
    vprm._check_lane_entries(lane_errs, "lane", "skips", [{"no": "nodeid"}])
    vprm._check_lane_entries(lane_errs, "lane", "skips", [{"nodeid": "t", "expected": False}])

    sha_errs = []
    vprm._verify_merged_sha(sha_errs, {"merged_sha": ""}, tmp_path, lambda r: MERGED_SHA)
    vprm._verify_merged_sha(sha_errs, {"merged_sha": MERGED_SHA}, tmp_path, lambda r: (_ for _ in ()).throw(RuntimeError("git")))
    vprm._verify_merge_parents(sha_errs, ["only-one"])
    vprm._verify_merge_parents(sha_errs, ["not a sha", "also bad!!"])
    vprm._verify_blocker_fix_shas(sha_errs, [])
    vprm._verify_blocker_fix_shas(sha_errs, ["zzzzzzz"])
    assert sha_errs
