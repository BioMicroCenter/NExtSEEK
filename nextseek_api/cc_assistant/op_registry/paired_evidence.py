"""Strict streaming loader for approved paired route evidence (Plan 005 Task 4)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nessie_tests import bayes_manifest as bm
from nessie_tests import corpus
from nessie_tests import export as nexport
from nessie_tests import runner

PINNED_ZIP_SHA256 = (
    "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814"
)

ZIP_MEMBER_GRADED = "testquestions/set3_final/grades/graded_rows.csv"
ZIP_MEMBER_FUNCTIONAL = (
    "testquestions/set3_final/hibayes/hibayes_functional_eval_inputs.csv"
)
ZIP_MEMBER_MANIFEST = "testquestions/set3_final/bayes_manifest.json"
ZIP_MEMBER_CORPUS = "testquestions/corpus/corpus.json"

REQUIRED_ZIP_MEMBERS: tuple[str, ...] = (
    ZIP_MEMBER_GRADED,
    ZIP_MEMBER_FUNCTIONAL,
    ZIP_MEMBER_MANIFEST,
    ZIP_MEMBER_CORPUS,
)

ALLOWED_ZIP_PREFIXES: frozenset[str] = frozenset(
    {"testquestions/set3_final/", "testquestions/corpus/"}
)

FORCED_ROUTE_BY_ARM = {"ns": "nextseek_query", "cc": "container_cc"}
FORCED_IMAGE_BY_ARM = dict(nexport.ARM_IMAGE)

DEFAULT_ZIP_PATH = Path(
    "/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07/testquestions.zip"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_PATH = REPO_ROOT / "nessie_tests" / "corpus.json"
CANONICAL_EVIDENCE_PATH = Path(__file__).resolve().parent / "route_example_evidence.json"

SCHEMA_VERSION = "route_example_evidence/v1"


class PairedEvidenceError(Exception):
    """Whole-ingest failure; no partial records."""


@dataclass(frozen=True)
class ZipMemberBytes:
    name: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_evidence_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def parse_strict_bool(value: str | None, *, field: str) -> bool:
    """Parse CSV booleans without Python truthiness traps."""
    if value is None:
        raise PairedEvidenceError(f"{field}: missing value")
    text = value.strip()
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    raise PairedEvidenceError(f"{field}: invalid boolean {value!r}")


def parse_optional_success(value: str | None, *, field: str) -> bool | None:
    """Tri-state human/LLM success: empty is not failure."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    lower = text.lower()
    if lower == "true":
        return True
    if lower in {"false", "fail"}:
        return False
    raise PairedEvidenceError(f"{field}: invalid success value {value!r}")


def parse_usefulness(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise PairedEvidenceError(f"usefulness_score: invalid numeric {value!r}") from exc


def arm_success(row: dict[str, str], *, forced_image: str) -> bool:
    if row.get("image") != forced_image:
        return False
    try:
        answer_provided = parse_strict_bool(row.get("answer_provided"), field="answer_provided")
        is_error = parse_strict_bool(row.get("is_error"), field="is_error")
        timed_out = parse_strict_bool(row.get("timed_out"), field="timed_out")
        runtime_success = parse_strict_bool(
            row.get("runtime_success"), field="runtime_success"
        )
    except PairedEvidenceError:
        return False
    if not answer_provided or is_error or timed_out or not runtime_success:
        return False
    human = parse_optional_success(row.get("human_success"), field="human_success")
    if human is False:
        return False
    llm = parse_optional_success(row.get("llm_success"), field="llm_success")
    if llm is False:
        return False
    return True


def _read_csv_rows(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _validate_zip_prefix(member: str) -> None:
    if not any(member.startswith(prefix) for prefix in ALLOWED_ZIP_PREFIXES):
        raise PairedEvidenceError(
            f"unexpected archive member prefix for {member!r}; "
            f"expected one of {sorted(ALLOWED_ZIP_PREFIXES)}"
        )


def stream_required_members(zip_path: Path) -> dict[str, ZipMemberBytes]:
    if not zip_path.is_file():
        raise PairedEvidenceError(f"missing paired source zip: {zip_path}")
    actual_zip_sha = sha256_file(zip_path)
    if actual_zip_sha != PINNED_ZIP_SHA256:
        raise PairedEvidenceError(
            f"zip sha256 mismatch: expected {PINNED_ZIP_SHA256}, got {actual_zip_sha}"
        )
    out: dict[str, ZipMemberBytes] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        missing = [name for name in REQUIRED_ZIP_MEMBERS if name not in names]
        if missing:
            raise PairedEvidenceError(f"zip missing required members: {missing}")
        for member in REQUIRED_ZIP_MEMBERS:
            _validate_zip_prefix(member)
            data = archive.read(member)
            out[member] = ZipMemberBytes(name=member, data=data)
    return out


def _corpus_authority(corpus_path: Path) -> tuple[str, dict[str, Any]]:
    if not corpus_path.is_file():
        raise PairedEvidenceError(f"missing corpus authority: {corpus_path}")
    fingerprint = runner.corpus_fingerprint(corpus_path)
    variants = corpus.load_all_definitions(corpus_path)
    index: dict[str, Any] = {}
    for variant in variants:
        if variant.id in index:
            raise PairedEvidenceError(f"duplicate corpus id {variant.id!r}")
        index[variant.id] = {
            "family": variant.family,
            "query_text": nexport.query_text(variant),
        }
    return fingerprint, index


def _manifest_selected_ids(manifest: bm.BayesManifest) -> list[str]:
    selected = list(manifest.run_meta.get("selected_ids") or [])
    if not selected:
        raise PairedEvidenceError("run_meta.selected_ids is empty")
    if len(selected) != len(set(selected)):
        raise PairedEvidenceError("run_meta.selected_ids contains duplicates")
    pair_ids = [pair.id for pair in manifest.pairs]
    if pair_ids != selected:
        raise PairedEvidenceError(
            "run_meta.selected_ids order must equal pairs[].id order"
        )
    return selected


def _validate_manifest_pairs(manifest: bm.BayesManifest, selected_ids: list[str]) -> None:
    if len(manifest.pairs) != len(selected_ids):
        raise PairedEvidenceError("pairs count must equal selected_ids count")
    seen: set[str] = set()
    for pair in manifest.pairs:
        if pair.id in seen:
            raise PairedEvidenceError(f"duplicate manifest pair id {pair.id!r}")
        seen.add(pair.id)
        if pair.ns is None or pair.cc is None:
            raise PairedEvidenceError(f"pair {pair.id!r} missing ns or cc arm")
        if pair.ns.id != pair.id or pair.cc.id != pair.id:
            raise PairedEvidenceError(f"pair {pair.id!r} arm ids must equal pair id")
        if pair.ns.family != pair.family or pair.cc.family != pair.family:
            raise PairedEvidenceError(f"pair {pair.id!r} family mismatch across arms")
        if pair.ns.route != FORCED_ROUTE_BY_ARM["ns"]:
            raise PairedEvidenceError(
                f"pair {pair.id!r} ns.route must be {FORCED_ROUTE_BY_ARM['ns']!r}"
            )
        if pair.cc.route != FORCED_ROUTE_BY_ARM["cc"]:
            raise PairedEvidenceError(
                f"pair {pair.id!r} cc.route must be {FORCED_ROUTE_BY_ARM['cc']!r}"
            )
        if pair.ns.route_source != "forced" or pair.cc.route_source != "forced":
            raise PairedEvidenceError(f"pair {pair.id!r} route_source must be forced")


def _expected_graded_keys(selected_ids: list[str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for query_id in selected_ids:
        keys.add((query_id, FORCED_ROUTE_BY_ARM["ns"]))
        keys.add((query_id, FORCED_ROUTE_BY_ARM["cc"]))
    return keys


def _expected_functional_keys(selected_ids: list[str]) -> set[str]:
    keys: set[str] = set()
    for query_id in selected_ids:
        keys.add(nexport.stage_b_query_id(query_id, "ns"))
        keys.add(nexport.stage_b_query_id(query_id, "cc"))
    return keys


def _index_graded_rows(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    indexed: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        query_id = row.get("query_id", "")
        image = row.get("image", "")
        key = (query_id, image)
        if key in indexed:
            raise PairedEvidenceError(f"duplicate graded row for {key!r}")
        indexed[key] = row
    return indexed


def _index_functional_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        query_id = row.get("query_id", "")
        if query_id in indexed:
            raise PairedEvidenceError(f"duplicate functional row for {query_id!r}")
        indexed[query_id] = row
    return indexed


def _arm_outcome(row: dict[str, str], *, arm: str) -> dict[str, Any]:
    forced_image = FORCED_IMAGE_BY_ARM[arm]
    usefulness = parse_usefulness(row.get("usefulness_score"))
    return {
        "image": row.get("image", ""),
        "answer_provided": parse_strict_bool(
            row.get("answer_provided"), field="answer_provided"
        ),
        "is_error": parse_strict_bool(row.get("is_error"), field="is_error"),
        "timed_out": parse_strict_bool(row.get("timed_out"), field="timed_out"),
        "runtime_success": parse_strict_bool(
            row.get("runtime_success"), field="runtime_success"
        ),
        "human_success": parse_optional_success(
            row.get("human_success"), field="human_success"
        ),
        "llm_success": parse_optional_success(
            row.get("llm_success"), field="llm_success"
        ),
        "usefulness_score": usefulness,
        "success": arm_success(row, forced_image=forced_image),
    }


def _audit_partition(
    records: list[dict[str, Any]], *, selected_ids: list[str]
) -> dict[str, list[str]]:
    success_by_id = {
        record["query_id"]: (record["ns"]["success"], record["cc"]["success"])
        for record in records
    }
    ns_only: list[str] = []
    cc_only: list[str] = []
    both_success: list[str] = []
    neither_success: list[str] = []
    for query_id in selected_ids:
        ns_ok, cc_ok = success_by_id[query_id]
        if ns_ok and cc_ok:
            both_success.append(query_id)
        elif ns_ok:
            ns_only.append(query_id)
        elif cc_ok:
            cc_only.append(query_id)
        else:
            neither_success.append(query_id)
    return {
        "ns_only": ns_only,
        "cc_only": cc_only,
        "both_success": both_success,
        "neither_success": neither_success,
    }


def ingest_paired_evidence(
    *,
    zip_path: Path,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
) -> dict[str, Any]:
    members = stream_required_members(zip_path)
    manifest = bm.BayesManifest.model_validate_json(
        members[ZIP_MEMBER_MANIFEST].data.decode("utf-8")
    )
    selected_ids = _manifest_selected_ids(manifest)
    _validate_manifest_pairs(manifest, selected_ids)

    corpus_fingerprint, corpus_index = _corpus_authority(corpus_path)

    graded_rows = _read_csv_rows(members[ZIP_MEMBER_GRADED].data)
    functional_rows = _read_csv_rows(members[ZIP_MEMBER_FUNCTIONAL].data)
    graded_index = _index_graded_rows(graded_rows)
    functional_index = _index_functional_rows(functional_rows)

    expected_graded = _expected_graded_keys(selected_ids)
    if set(graded_index) != expected_graded:
        raise PairedEvidenceError("graded-row key set mismatch")

    expected_functional = _expected_functional_keys(selected_ids)
    if set(functional_index) != expected_functional:
        raise PairedEvidenceError("functional-input key set mismatch")

    pair_by_id = {pair.id: pair for pair in manifest.pairs}
    records: list[dict[str, Any]] = []
    for query_id in selected_ids:
        pair = pair_by_id[query_id]
        if query_id not in corpus_index:
            raise PairedEvidenceError(f"selected id {query_id!r} missing from corpus")
        corpus_entry = corpus_index[query_id]
        if corpus_entry["family"] != pair.family:
            raise PairedEvidenceError(
                f"corpus family mismatch for {query_id!r}: "
                f"{corpus_entry['family']!r} != {pair.family!r}"
            )

        ns_graded = graded_index[(query_id, FORCED_ROUTE_BY_ARM["ns"])]
        cc_graded = graded_index[(query_id, FORCED_ROUTE_BY_ARM["cc"])]
        ns_functional = functional_index[nexport.stage_b_query_id(query_id, "ns")]
        cc_functional = functional_index[nexport.stage_b_query_id(query_id, "cc")]

        for label, graded, functional in (
            ("ns", ns_graded, ns_functional),
            ("cc", cc_graded, cc_functional),
        ):
            if graded.get("task_family") != pair.family:
                raise PairedEvidenceError(
                    f"{query_id} {label} graded task_family mismatch"
                )
            if functional.get("task_family") != pair.family:
                raise PairedEvidenceError(
                    f"{query_id} {label} functional task_family mismatch"
                )
            if functional.get("query_text") != corpus_entry["query_text"]:
                raise PairedEvidenceError(
                    f"{query_id} {label} functional query_text drift from corpus"
                )

        records.append(
            {
                "query_id": query_id,
                "task_family": pair.family,
                "query_text": corpus_entry["query_text"],
                "corpus_fingerprint": corpus_fingerprint,
                "ns": _arm_outcome(ns_graded, arm="ns"),
                "cc": _arm_outcome(cc_graded, arm="cc"),
            }
        )

    audit = _audit_partition(records, selected_ids=selected_ids)
    covered = set(
        audit["ns_only"]
        + audit["cc_only"]
        + audit["both_success"]
        + audit["neither_success"]
    )
    if covered != set(selected_ids):
        raise PairedEvidenceError("audit partition must exhaustively cover selected_ids")

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "zip_sha256": PINNED_ZIP_SHA256,
            "members": {
                name: members[name].sha256 for name in REQUIRED_ZIP_MEMBERS
            },
        },
        "selected_ids": selected_ids,
        "corpus_fingerprint": corpus_fingerprint,
        "records": records,
        "audit": audit,
    }


def load_committed_evidence(path: Path = CANONICAL_EVIDENCE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise PairedEvidenceError(f"missing committed evidence: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_committed_structure(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PairedEvidenceError("schema_version mismatch")
    selected_ids = payload.get("selected_ids")
    records = payload.get("records")
    audit = payload.get("audit")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise PairedEvidenceError("selected_ids missing or empty")
    if not isinstance(records, list) or len(records) != len(selected_ids):
        raise PairedEvidenceError("records length mismatch")
    record_ids = [record["query_id"] for record in records]
    if record_ids != selected_ids:
        raise PairedEvidenceError("record ids must equal selected_ids order")
    if not isinstance(audit, dict):
        raise PairedEvidenceError("audit section missing")
    parts = ("ns_only", "cc_only", "both_success", "neither_success")
    seen: set[str] = set()
    recomputed: list[str] = []
    for part in parts:
        ids = audit.get(part)
        if not isinstance(ids, list):
            raise PairedEvidenceError(f"audit.{part} must be a list")
        for query_id in ids:
            if query_id in seen:
                raise PairedEvidenceError(f"audit partition overlap at {query_id!r}")
            seen.add(query_id)
            recomputed.append(query_id)
    if seen != set(selected_ids):
        raise PairedEvidenceError("audit partition is not an exhaustive cover of selected_ids")
    for record in records:
        for arm in ("ns", "cc"):
            outcome = record[arm]
            recomputed_success = arm_success(
                {
                    "image": outcome["image"],
                    "answer_provided": "true" if outcome["answer_provided"] else "false",
                    "is_error": "true" if outcome["is_error"] else "false",
                    "timed_out": "true" if outcome["timed_out"] else "false",
                    "runtime_success": "true"
                    if outcome["runtime_success"]
                    else "false",
                    "human_success": _success_to_csv(outcome["human_success"]),
                    "llm_success": _success_to_csv(outcome["llm_success"]),
                },
                forced_image=FORCED_IMAGE_BY_ARM[arm],
            )
            if outcome["success"] != recomputed_success:
                raise PairedEvidenceError(
                    f"{record['query_id']} {arm} success mismatch in committed evidence"
                )


def _success_to_csv(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def check_export(
    *,
    evidence_path: Path = CANONICAL_EVIDENCE_PATH,
    zip_path: Path = DEFAULT_ZIP_PATH,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
) -> None:
    committed = load_committed_evidence(evidence_path)
    validate_committed_structure(committed)
    expected_bytes = canonical_evidence_bytes(committed)
    actual_bytes = evidence_path.read_bytes()
    if actual_bytes != expected_bytes:
        raise SystemExit(f"evidence check failed: non-canonical bytes at {evidence_path}")
    if zip_path.is_file():
        fresh = ingest_paired_evidence(zip_path=zip_path, corpus_path=corpus_path)
        if canonical_evidence_bytes(fresh) != expected_bytes:
            raise SystemExit(
                f"evidence check failed: committed evidence differs from source ingest at {evidence_path}"
            )
    else:
        source = committed.get("source", {})
        if source.get("zip_sha256") != PINNED_ZIP_SHA256:
            raise SystemExit("evidence check failed: recorded zip sha256 mismatch")
        members = source.get("members")
        if not isinstance(members, dict):
            raise SystemExit("evidence check failed: source.members missing")


def write_export(
    *,
    zip_path: Path,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    evidence_path: Path = CANONICAL_EVIDENCE_PATH,
) -> bytes:
    payload = ingest_paired_evidence(zip_path=zip_path, corpus_path=corpus_path)
    rendered = canonical_evidence_bytes(payload)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(rendered)
    return rendered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize and check paired route example evidence."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate committed evidence.")
    mode.add_argument("--write", action="store_true", help="Write normalized evidence JSON.")
    parser.add_argument("--zip-path", type=Path, default=DEFAULT_ZIP_PATH)
    parser.add_argument("--corpus-path", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=CANONICAL_EVIDENCE_PATH,
        help="Committed route_example_evidence.json path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.check:
        check_export(
            evidence_path=args.evidence_path,
            zip_path=args.zip_path,
            corpus_path=args.corpus_path,
        )
        return 0
    write_export(
        zip_path=args.zip_path,
        corpus_path=args.corpus_path,
        evidence_path=args.evidence_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
