#!/usr/bin/env python3
"""Plan 018 V4-5 verifier — generation store + real-store oracle checks."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_REQUIRED_REALSTORE_ORACLES = frozenset(
    {
        "stale_cas",
        "two_activators",
        "parent_mismatch_refused",
        "immutable_overwrite_refused",
        "rollback",
        "reader_single_hash",
        "corruption",
        "taxonomy_corpus_incompat",
        "partial_publish_refused",
        "crash_publish_boundary",
        "crash_activation_boundary",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", default=str(_REPO / "evidence/plan018-v4-5-verifier.sidecar.json"))
    parser.add_argument("--log", default=str(_REPO / "evidence/plan018-v4-5-verifier.log"))
    args = parser.parse_args()

    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    migration = _REPO / "nextseek_api/migrations/0015_v4_5_generation_audit_and_turn_pin.py"
    record("migration_0015_exists", migration.is_file(), str(migration))

    overlay = _REPO / "nextseek_api/cc_assistant/risk_overlay.py"
    record("risk_overlay_exists", overlay.is_file(), str(overlay))
    if overlay.is_file():
        text = overlay.read_text()
        assignments = re.findall(r"may_reroute\s*=\s*(True|False)", text)
        record(
            "may_reroute_only_false",
            assignments and all(value == "False" for value in assignments),
            ",".join(assignments) or "none",
        )

    sidecars = [
        "plan018-v4-5-phase0-publish.json",
        "plan018-v4-5-prereq.json",
        "plan018-v4-5-remediation-lane-c.sidecar.json",
    ]
    for name in sidecars:
        path = _REPO / "evidence" / name
        record(f"evidence_{name}", path.is_file(), str(path))
        if path.is_file():
            data = json.loads(path.read_text())
            record(f"{name}_gate_pass", data.get("gate") == "PASS", str(data.get("gate")))

    realstore = _REPO / "evidence/plan018-v4-5-realstore.sidecar.json"
    record("realstore_sidecar_exists", realstore.is_file(), str(realstore))
    if realstore.is_file():
        rs = json.loads(realstore.read_text())
        record("realstore_gate_pass", rs.get("gate") == "PASS", str(rs.get("gate")))
        record("mysql_isolation_documented", bool(rs.get("isolation_level")), rs.get("isolation_level", ""))
        oracle_set = set(rs.get("oracles") or [])
        missing = sorted(_REQUIRED_REALSTORE_ORACLES - oracle_set)
        record(
            "realstore_oracles_complete",
            not missing,
            "missing=" + ",".join(missing) if missing else "all",
        )

    gs = (_REPO / "nextseek_api/eval/generation_store.py").read_text()
    record("cas_uses_empty_active_hash", "EMPTY_ACTIVE_HASH" in gs, "token")
    record("validate_before_activate", "require_valid_for_activation" in gs, "wired")
    record("rollback_helper", "def rollback_generation" in gs, "present")
    record("turn_pin", "def pin_generation_for_turn" in gs, "present")
    record("publish_abort_hook", "PublishAbort" in gs and "set_test_abort_publish_after_generation" in gs, "present")
    record("activation_abort_hook", "ActivationAbort" in gs and "set_test_abort_activate_after_pointer_mutate" in gs, "present")

    pub = (_REPO / "nextseek_api/eval/publish.py").read_text()
    record("publish_no_band_from_status", "_band_from_status" not in pub, "absent")

    record(
        "no_stale_test_settings_lane_m",
        not (_REPO / "dmac/test_settings_lane_m.py").is_file(),
        "absent",
    )

    # Negative self-check: corrupt stored generation must fail validation (no skip-as-pass).
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.test_settings")
    try:
        import django
        from django.core.management import call_command

        django.setup()
        call_command("migrate", "--run-syncdb", verbosity=0, interactive=False)
        from nextseek_api.eval.generation_store import GenerationManifest, publish_generation
        from nextseek_api.eval.generation_validation import validate_generation_for_activation
        from nextseek_api.eval.paired_run_registry import register_paired_run

        paired_run_id = "verifier-negative-run"
        register_paired_run(
            paired_run_id=paired_run_id,
            schema_version="v1",
            content_hash="0" * 64,
        )

        manifest = GenerationManifest(
            input_hash="verifier-negative",
            attempt_hash="verifier-negative-a",
            aggregate_hash="verifier-negative-g",
            config_fingerprint="verifier-negative-cfg",
            decision_status="activated_all",
            groups=[
                {
                    "name": "sample_search",
                    "route": "container_cc",
                    "posterior_mean": 0.9,
                    "band": "Reliable",
                    "n_total": 10,
                }
            ],
            compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "verifier-negative"},
            counts={"retained_pairs": 10},
            source_provenance={
                "paired_run_id": paired_run_id,
                "evidence_kind": "paired_experimental",
                "route_source": "forced",
            },
        )
        generation = publish_generation(manifest)
        generation.generation_hash = "0" * 64
        generation.save(update_fields=["generation_hash"])
        result = validate_generation_for_activation(generation)
        record(
            "negative_validation_fails",
            not result.ok and any("hash" in r for r in result.reasons),
            str(result.reasons[:2]),
        )
    except Exception as exc:
        record("negative_validation_fails", False, f"runtime required: {exc}")

    sidecar_path = Path(args.sidecar)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "plan018-v4-5-verifier/v1",
        "gate": "PASS" if not errors else "FAIL",
        "checks_passed": sum(1 for c in checks if c["pass"]),
        "checks_total": len(checks),
        "errors": errors,
        "checks": checks,
        "paid_or_live_resources_used": False,
    }
    sidecar_path.write_text(json.dumps(payload, indent=2) + "\n")
    Path(args.log).write_text(
        "\n".join(
            [f"{'PASS' if c['pass'] else 'FAIL'} {c['name']}: {c['detail']}" for c in checks]
        )
        + "\n"
    )
    print(json.dumps({"gate": payload["gate"], "checks": f"{payload['checks_passed']}/{payload['checks_total']}"}))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
