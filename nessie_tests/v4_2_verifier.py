"""Plan 018 V4-2 set3_final replay verifier (no route execution).

Replays transferred `bayes_manifest.json` through strict producer parsers,
checks V13-A identities, pair conservation, and route-trace integrity.
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from nessie_tests import bayes_manifest as bm
from nessie_tests import runner

V13A_DELIVERY = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")
V13A_ZIP = V13A_DELIVERY / "testquestions.zip"
SET3_ZIP_MEMBER = "testquestions/set3_final/bayes_manifest.json"

V13A_EXPECTED = {
    "zip_sha256": "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814",
    "manifest_sha256": "d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2",
    "corpus_sha256": "99efa7a10f2d418190a4a29eb550fea9927037a1b3844a6bc319017609155652",
    "bayes_manifest_sha256": "b2afcb1cbfcf908662419db49aa16f82f22afbd9533b7a25053acf6a5641e6c0",
    "selected_count": 149,
    "pair_count": 149,
    "arm_count": 298,
}

FORCED_ROUTES = {"ns": "nextseek_query", "cc": "container_cc"}


@dataclass
class VerifierReport:
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def ok(self, name: str, **detail: Any) -> None:
        self.checks.append({"name": name, "pass": True, **detail})

    def fail(self, name: str, msg: str, **detail: Any) -> None:
        self.errors.append(f"{name}: {msg}")
        self.checks.append({"name": name, "pass": False, "message": msg, **detail})

    @property
    def passed(self) -> bool:
        return not self.errors


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_set3_bayes_bytes(*, zip_path: Path = V13A_ZIP) -> bytes:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.read(SET3_ZIP_MEMBER)


def load_set3_bayes_manifest(*, zip_path: Path = V13A_ZIP) -> bm.BayesManifest:
    raw = load_set3_bayes_bytes(zip_path=zip_path)
    return bm.BayesManifest.model_validate(orjson.loads(raw))


def verify_v13a_identities(report: VerifierReport, *, zip_path: Path = V13A_ZIP) -> None:
    manifest_path = V13A_DELIVERY / "MANIFEST.json"
    if not zip_path.is_file():
        report.fail("v13a_zip_present", f"missing {zip_path}")
        return
    zip_sha = sha256_file(zip_path)
    if zip_sha != V13A_EXPECTED["zip_sha256"]:
        report.fail("v13a_zip_sha256", f"got {zip_sha}")
    else:
        report.ok("v13a_zip_sha256", sha256=zip_sha)

    if manifest_path.is_file():
        man_sha = sha256_file(manifest_path)
        if man_sha != V13A_EXPECTED["manifest_sha256"]:
            report.fail("v13a_manifest_sha256", f"got {man_sha}")
        else:
            report.ok("v13a_manifest_sha256", sha256=man_sha)
    else:
        report.fail("v13a_manifest_present", f"missing {manifest_path}")

    corpus_path = Path(__file__).resolve().parent / "corpus.json"
    corpus_sha = sha256_file(corpus_path)
    if corpus_sha != V13A_EXPECTED["corpus_sha256"]:
        report.fail("v13a_corpus_sha256", f"got {corpus_sha}")
    else:
        report.ok("v13a_corpus_sha256", sha256=corpus_sha)

    bayes_bytes = load_set3_bayes_bytes(zip_path=zip_path)
    bayes_sha = sha256_bytes(bayes_bytes)
    if bayes_sha != V13A_EXPECTED["bayes_manifest_sha256"]:
        report.fail("v13a_set3_bayes_sha256", f"got {bayes_sha}")
    else:
        report.ok("v13a_set3_bayes_sha256", sha256=bayes_sha)


def verify_conservation(m: bm.BayesManifest, report: VerifierReport) -> None:
    pair_count = len(m.pairs)
    if pair_count != V13A_EXPECTED["pair_count"]:
        report.fail("pair_count", f"expected {V13A_EXPECTED['pair_count']}, got {pair_count}")
    else:
        report.ok("pair_count", count=pair_count)

    selected = m.run_meta.get("selected_ids") or []
    if len(selected) != V13A_EXPECTED["selected_count"]:
        report.fail("selected_count", f"expected {V13A_EXPECTED['selected_count']}, got {len(selected)}")
    else:
        report.ok("selected_count", count=len(selected))

    arms = sum(1 for p in m.pairs for arm in ("ns", "cc") if getattr(p, arm) is not None)
    if arms != V13A_EXPECTED["arm_count"]:
        report.fail("arm_count", f"expected {V13A_EXPECTED['arm_count']}, got {arms}")
    else:
        report.ok("arm_count", count=arms)

    complete = sum(1 for p in m.pairs if p.ns is not None and p.cc is not None)
    if complete != pair_count:
        report.fail("complete_pairs", f"expected all {pair_count} complete, got {complete}")
    else:
        report.ok("complete_pairs", count=complete)

    fp = m.run_meta.get("corpus_fingerprint")
    if fp != V13A_EXPECTED["corpus_sha256"]:
        report.fail("corpus_fingerprint_match", f"manifest {fp!r} != corpus {V13A_EXPECTED['corpus_sha256']!r}")
    else:
        report.ok("corpus_fingerprint_match", fingerprint=fp)

    live_fp = runner.corpus_fingerprint(Path(__file__).resolve().parent / "corpus.json")
    if live_fp != fp:
        report.fail("corpus_fingerprint_live", f"checkout {live_fp!r} != manifest {fp!r}")
    else:
        report.ok("corpus_fingerprint_live", fingerprint=live_fp)


def verify_pair_identities(m: bm.BayesManifest, report: VerifierReport) -> None:
    err = validate_unique_pair_ids(m)
    if err:
        report.fail("duplicate_pair_ids", err)
    else:
        report.ok("duplicate_pair_ids")

    selected = set(m.run_meta.get("selected_ids") or [])
    manifest_ids = {p.id for p in m.pairs}
    if manifest_ids != selected:
        extra = manifest_ids - selected
        missing = selected - manifest_ids
        report.fail(
            "selected_ids_match_pairs",
            f"mismatch extra={len(extra)} missing={len(missing)}",
        )
    else:
        report.ok("selected_ids_match_pairs", count=len(m.pairs))

    for p in m.pairs:
        if p.ns is not None and p.ns.id != p.id:
            report.fail("referential_ns_id", f"pair {p.id!r} ns.id={p.ns.id!r}")
            return
        if p.cc is not None and p.cc.id != p.id:
            report.fail("referential_cc_id", f"pair {p.id!r} cc.id={p.cc.id!r}")
            return
        if p.ns is not None and p.ns.family != p.family:
            report.fail("referential_ns_family", f"pair {p.id!r}")
            return
        if p.cc is not None and p.cc.family != p.family:
            report.fail("referential_cc_family", f"pair {p.id!r}")
            return
    report.ok("referential_integrity", pairs=len(m.pairs))


def verify_route_traces(m: bm.BayesManifest, report: VerifierReport) -> None:
    mismatches = 0
    sticky_overrides = 0
    for p in m.pairs:
        for arm_name, entry in (("ns", p.ns), ("cc", p.cc)):
            if entry is None:
                continue
            expected_route = FORCED_ROUTES[arm_name]
            if entry.route != expected_route:
                mismatches += 1
            if entry.route_source != "forced":
                if entry.route_source == "sticky":
                    sticky_overrides += 1
                else:
                    mismatches += 1
            if entry.route_sources and any(s != "forced" for s in entry.route_sources):
                mismatches += 1
            ns_tid = p.ns.task_ids if p.ns else []
            cc_tid = p.cc.task_ids if p.cc else []
            if ns_tid and cc_tid and set(ns_tid) & set(cc_tid):
                report.fail("same_execution_task_id", f"pair {p.id!r} shared task_ids")
                return
    if sticky_overrides:
        report.fail("sticky_override", f"{sticky_overrides} arms had sticky route_source")
    elif mismatches:
        report.fail("route_trace_forced", f"{mismatches} arm route/source mismatches")
    else:
        report.ok("route_trace_forced", arms=V13A_EXPECTED["arm_count"])


def verify_rejection_cases(report: VerifierReport) -> None:
    """Hermetic negative cases — parsers/adapters must fail closed."""

    def _entry(vid: str, route: str, source: str = "forced") -> dict[str, Any]:
        return {
            "id": vid,
            "family": "f",
            "tier": "full",
            "status": "passed",
            "route": route,
            "route_source": source,
            "route_sources": [source],
            "task_ids": [f"{vid}-{route}"],
        }

    base_pair = {
        "id": "x.y",
        "family": "f",
        "hibayes_subtype": None,
        "ns": _entry("x.y", "nextseek_query"),
        "cc": _entry("x.y", "container_cc"),
    }

    dup_payload = {
        "run_meta": {"mode": "bayesian"},
        "pairs": [base_pair, dict(base_pair)],
    }
    m_dup = bm.BayesManifest.model_validate(dup_payload)
    err = validate_unique_pair_ids(m_dup)
    if err is None:
        report.fail("reject_duplicate_pairs", "duplicate ids accepted")
    else:
        report.ok("reject_duplicate_pairs", message=err)

    swapped = dict(base_pair)
    swapped["ns"] = _entry("x.y", "container_cc")
    swapped["cc"] = _entry("x.y", "nextseek_query")
    m_swapped = bm.BayesManifest.model_validate({"run_meta": {}, "pairs": [swapped]})
    err = validate_manifest_route_policy(m_swapped)
    if err is None:
        report.fail("reject_swapped_routes", "swapped arms accepted")
    else:
        report.ok("reject_swapped_routes", message=err)

    partial = dict(base_pair)
    partial["cc"] = None
    m_partial = bm.BayesManifest.model_validate({"run_meta": {}, "pairs": [partial]})
    done = bm.completed_arms(m_partial)
    if done != {("x.y", "ns")}:
        report.fail("partial_pair_conservation", f"completed_arms={done!r}")
    else:
        report.ok("partial_pair_conservation")

    copied = dict(base_pair)
    copied["cc"] = _entry("x.y", "container_cc")
    copied["cc"]["task_ids"] = copied["ns"]["task_ids"]
    m_copied = bm.BayesManifest.model_validate({"run_meta": {}, "pairs": [copied]})
    err = validate_manifest_route_policy(m_copied)
    if err is None:
        report.fail("reject_copied_execution", "shared task_ids accepted")
    else:
        report.ok("reject_copied_execution", message=err)

    bad_key = {"run_meta": {}, "pairs": [base_pair], "extra_field": True}
    try:
        bm.BayesManifest.model_validate(bad_key)
        report.fail("reject_unknown_key", "extra=forbid bypassed")
    except ValidationError:
        report.ok("reject_unknown_key")

    sticky = dict(base_pair)
    sticky["ns"] = _entry("x.y", "nextseek_query", source="sticky")
    m_sticky = bm.BayesManifest.model_validate({"run_meta": {}, "pairs": [sticky]})
    err = validate_manifest_route_policy(m_sticky)
    if err is None:
        report.fail("reject_sticky_override", "sticky source accepted for forced arm")
    else:
        report.ok("reject_sticky_override", message=err)


def validate_unique_pair_ids(m: bm.BayesManifest) -> str | None:
    ids = [p.id for p in m.pairs]
    if len(ids) != len(set(ids)):
        return "duplicate pair ids"
    return None


def validate_manifest_route_policy(m: bm.BayesManifest) -> str | None:
    """Return an error string if any arm violates forced-route policy."""
    err = validate_unique_pair_ids(m)
    if err:
        return err
    for p in m.pairs:
        for arm_name, entry in (("ns", p.ns), ("cc", p.cc)):
            if entry is None:
                continue
            expected = FORCED_ROUTES[arm_name]
            if entry.route != expected:
                return f"{p.id}/{arm_name}: route {entry.route!r} != {expected!r}"
            if entry.route_source != "forced":
                return f"{p.id}/{arm_name}: route_source {entry.route_source!r} != forced"
            if entry.route_sources and any(s != "forced" for s in entry.route_sources):
                return f"{p.id}/{arm_name}: route_sources {entry.route_sources!r}"
        ns_tid = p.ns.task_ids if p.ns else []
        cc_tid = p.cc.task_ids if p.cc else []
        if ns_tid and cc_tid and set(ns_tid) & set(cc_tid):
            return f"{p.id}: shared task_ids between arms"
    return None


def run_verifier(*, zip_path: Path = V13A_ZIP) -> VerifierReport:
    report = VerifierReport()
    verify_v13a_identities(report, zip_path=zip_path)
    if not report.passed:
        return report
    m = load_set3_bayes_manifest(zip_path=zip_path)
    verify_conservation(m, report)
    verify_pair_identities(m, report)
    verify_route_traces(m, report)
    err = validate_manifest_route_policy(m)
    if err:
        report.fail("route_policy_set3", err)
    else:
        report.ok("route_policy_set3")
    verify_rejection_cases(report)
    report.ok(
        "future_dual_route_note",
        note=(
            "Future promoted corpus occurrences still require independent dual "
            "forced routes at use time; this gate replays stored set3_final only."
        ),
    )
    return report
