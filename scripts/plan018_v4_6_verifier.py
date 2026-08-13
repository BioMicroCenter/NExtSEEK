#!/usr/bin/env python3
"""Plan 018 V4-6 verifier — classifier/router split + call-table + selector."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_CALL_TABLE_ROWS = (
    "flag_off",
    "flag_on_unrelated",
    "flag_on_pretransport_invalid",
    "flag_on_posttransport_failure",
    "flag_on_decisive_posterior",
    "flag_on_indecisive_fallback",
    "sticky_override",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", default=str(_REPO / "evidence/plan018-v4-6-verifier.sidecar.json"))
    parser.add_argument("--log", default=str(_REPO / "evidence/plan018-v4-6-verifier.log"))
    args = parser.parse_args()

    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    # Phase 0 + prereq sidecars
    for name in (
        "plan018-v4-6-phase0-publish.json",
        "plan018-v4-6-prereq.json",
        "plan018-v4-6-family-labels.sidecar.json",
        "plan018-v4-6-calltable.sidecar.json",
        "plan018-v4-6-selector.sidecar.json",
    ):
        path = _REPO / "evidence" / name
        record(f"evidence_{name}", path.is_file(), str(path))
        if path.is_file():
            data = json.loads(path.read_text())
            record(f"{name}_gate_pass", data.get("gate") == "PASS", str(data.get("gate")))

    inventory = _REPO / "evidence/plan018-v4-6-callsite-inventory.json"
    record("inventory_exists", inventory.is_file(), str(inventory))
    if inventory.is_file():
        inv = json.loads(inventory.read_text())
        record("inventory_complete", inv.get("status") == "complete", inv.get("status", ""))
        record("inventory_seams", len(inv.get("seams") or []) >= 6, str(len(inv.get("seams") or [])))

    router_a = _REPO / "dmac_assistant/baml_src/router.baml"
    router_b = _REPO / "docker/cc-runtime/baml_src/router.baml"
    record("router_baml_dual_identity", _sha(router_a) == _sha(router_b), _sha(router_a))

    classifier_a = _REPO / "dmac_assistant/baml_src/classifier.baml"
    classifier_b = _REPO / "docker/cc-runtime/baml_src/classifier.baml"
    record("classifier_baml_exists", classifier_a.is_file() and classifier_b.is_file(), "both")
    record("classifier_baml_dual_identity", classifier_a.read_bytes() == classifier_b.read_bytes(), "bytes")

    if classifier_a.is_file():
        body = classifier_a.read_text().split("class ClassificationDecision")[1].split("function")[0]
        record("classifier_no_route_field", "route" not in body and "model_class" not in body, body[:80])

    for module in ("family_labels.py", "baml_introspect.py", "posterior_selector.py", "transport_trace.py"):
        record(f"module_{module}", (_REPO / "nextseek_api/cc_assistant" / module).is_file(), module)

    fl = (_REPO / "nextseek_api/cc_assistant/family_labels.py").read_text()
    record("no_route_capabilities_read", "route_capabilities" not in fl, "grep")

    ps = (_REPO / "nextseek_api/cc_assistant/posterior_selector.py").read_text()
    record(
        "posterior_flag_default_off",
        'getattr(settings, "NEXTSEEK_POSTERIOR_ROUTING_ENABLED", False)' in ps,
        "getattr default False",
    )

    overlay = (_REPO / "nextseek_api/cc_assistant/risk_overlay.py").read_text()
    assignments = re.findall(r"may_reroute\s*=\s*(True|False)", overlay)
    record("may_reroute_only_false", assignments and all(v == "False" for v in assignments), ",".join(assignments))

    rmp = (_REPO / "nextseek_api/eval/router_models_proposal.py").read_text()
    record("route_source_posterior", '"posterior"' in rmp or "'posterior'" in rmp, "enum")

    lane_junit = _REPO / "evidence/plan018-v4-6-lane-c.junit.xml"
    record("lane_c_junit_exists", lane_junit.is_file(), str(lane_junit))
    if lane_junit.is_file():
        try:
            suite = ElementTree.parse(lane_junit).getroot().find("testsuite")
            tests = int(suite.attrib.get("tests", "0")) if suite is not None else 0
            failures = int(suite.attrib.get("failures", "-1")) if suite is not None else -1
            errors_count = int(suite.attrib.get("errors", "-1")) if suite is not None else -1
            record(
                "lane_c_junit_all_passed",
                tests > 0 and failures == 0 and errors_count == 0,
                f"tests={tests},failures={failures},errors={errors_count}",
            )
        except ElementTree.ParseError as exc:
            record("lane_c_junit_all_passed", False, f"invalid junit: {exc}")

    calltable = _REPO / "evidence/plan018-v4-6-calltable.sidecar.json"
    if calltable.is_file():
        rows = set(json.loads(calltable.read_text()).get("call_table_rows") or [])
        missing = sorted(set(_CALL_TABLE_ROWS) - rows)
        record("call_table_rows_complete", not missing, ",".join(missing) if missing else "all")

    sidecar = {
        "schema": "plan018-v4-6-verifier/v1",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": "PASS" if not errors else "FAIL",
        "checks_passed": sum(1 for c in checks if c.get("pass")),
        "checks_total": len(checks),
        "errors": errors,
        "checks": checks,
        "hashes": {
            "router_baml_sha256": _sha(router_a),
            "classifier_baml_sha256": _sha(classifier_a),
            "corpus_sha256": _sha(_REPO / "nessie_tests/corpus.json"),
        },
        "paid_or_live_resources_used": False,
    }
    Path(args.sidecar).write_text(json.dumps(sidecar, indent=2) + "\n")
    Path(args.log).write_text(
        f"V4-6 verifier {'PASS' if not errors else 'FAIL'} "
        f"({sidecar['checks_passed']}/{sidecar['checks_total']})\n"
        + ("\n".join(errors) if errors else "")
        + "\n"
    )
    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1
    print(f"V4-6 verifier PASS ({sidecar['checks_passed']} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
