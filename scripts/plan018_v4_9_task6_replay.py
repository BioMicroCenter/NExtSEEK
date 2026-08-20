#!/usr/bin/env python3
"""Build, run, and validate the bounded Plan 018 V4-9 Task 6 replay."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")
APP_IMAGE = "nextseek-nextseek:latest"
APP_IMAGE_ID = "sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651"
EVAL_IMAGE = "nextseek-eval:v4-4"
EVAL_IMAGE_ID = "sha256:0045e7dbb3d020865cf76e92ab3eebecfc176558ec4a999a7a8b9bfed8d961ab"
TASK_IMAGE = "nextseek-eval:plan018-v4-9-task6"
RESULT = "evidence/plan018-v4-9-task6-replay.json"
JUNIT = "evidence/plan018-v4-9-task6.junit.xml"
EVIDENCE = "evidence/plan018-v4-9-task6-evidence.json"
MAX_WALL_S = 300.0

DELIVERY_FILES = {
    "testquestions.zip": {
        "size": 66_473_692,
        "sha256": "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814",
    },
    "MANIFEST.json": {
        "size": 2_375_457,
        "sha256": "d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2",
    },
    "artifact_validity_set3_final.csv": {
        "size": 44_853,
        "sha256": "7d8859bd206d1c932773cc1d2d0791341a3eb54bdbecc32ea250a58f1827f693",
    },
}

EXPECTED_MEMBER_SHA256 = {
    "corpus/corpus.json": "99efa7a10f2d418190a4a29eb550fea9927037a1b3844a6bc319017609155652",
    "set3_final/bayes_manifest.json": "b2afcb1cbfcf908662419db49aa16f82f22afbd9533b7a25053acf6a5641e6c0",
    "set3_final/hibayes/hibayes_eval_rows_ns.csv": "3ffa3bb85982430e68fe9933ab01d248ec9b6610486a4490ed050905647d4d72",
    "set3_final/hibayes/hibayes_eval_rows_cc.csv": "ac2bfac379392232b4ce98a81e00cfeb06e8938806a50094ec7cfc94228f4859",
    "set3_final/hibayes/hibayes_functional_usefulness_human_ns.csv": "c1ed72e72b2fa1811d15c1fb8a88b8af791072947038901c4b0d356b8e0e3cef",
    "set3_final/hibayes/hibayes_functional_usefulness_human_cc.csv": "b40d13e3a1bcc2ed0fcd607c3fc6c4fe1ac6a601129cf7978346da1d4edec76b",
}

CONTROL_FILES = (
    "docker/eval-task6/Dockerfile",
    "nextseek_api/assistant/task6_app.py",
    "nextseek_api/eval/task6_settings.py",
    "nextseek_api/eval/task6_replay.py",
    "nextseek_api/eval/tests/test_v4_9_task6_replay.py",
    "scripts/plan018_v4_9_task6_replay.py",
    "scripts/test_plan018_v4_9_task6_replay.py",
    "nextseek_api/eval/human_grade_fit.py",
    "nextseek_api/eval/attempt_store.py",
    "nextseek_api/eval/stage_c_runner.py",
    "nextseek_api/eval/judge.py",
    "nextseek_api/eval/fit/v14/combined.py",
    "nextseek_api/eval/generation_store.py",
    "nextseek_api/cc_assistant/posterior_selector.py",
)


class GateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def verify_delivery(delivery: Path = DELIVERY) -> dict[str, dict[str, int | str]]:
    """Authenticate delivery containers before any JSON/ZIP/CSV parsing."""
    verified: dict[str, dict[str, int | str]] = {}
    for name, expected in DELIVERY_FILES.items():
        path = delivery / name
        if not path.is_file():
            raise GateError(f"required transferred evidence missing: {path}")
        actual_size = path.stat().st_size
        actual_hash = sha256(path)
        if actual_size != expected["size"] or actual_hash != expected["sha256"]:
            raise GateError(
                f"transferred evidence identity mismatch for {name}: "
                f"size={actual_size}, sha256={actual_hash}"
            )
        verified[name] = {"size": actual_size, "sha256": actual_hash}
    return verified


def command(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def image_id(image: str) -> str:
    result = command("docker", "image", "inspect", image, "--format", "{{.Id}}")
    if result.returncode:
        raise GateError(f"cannot inspect image {image}: {result.stdout}")
    return result.stdout.strip()


def junit_counts(path: Path) -> tuple[tuple[str, ...], dict[str, int]]:
    root = ET.fromstring(path.read_bytes())
    nodes: list[str] = []
    counts = Counter(tests=0, passed=0, failed=0, errors=0, skipped=0, xfail=0)
    for case in root.findall(".//testcase"):
        counts["tests"] += 1
        classname = case.attrib.get("classname", "").replace(".", "/")
        if not classname:
            raise GateError("Task 6 JUnit testcase omitted its source identity")
        name = html.unescape(case.attrib.get("name", ""))
        nodes.append(f"{classname}.py::{name}")
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif (skipped := case.find("skipped")) is not None:
            counts["xfail" if skipped.get("type") == "pytest.xfail" else "skipped"] += 1
        else:
            counts["passed"] += 1
    counts["deselected"] = 0
    return tuple(nodes), dict(counts)


def run(root: Path = ROOT, delivery: Path = DELIVERY) -> None:
    started = time.monotonic()
    delivery_identity = verify_delivery(delivery)
    if image_id(APP_IMAGE) != APP_IMAGE_ID or image_id(EVAL_IMAGE) != EVAL_IMAGE_ID:
        raise GateError("Task 6 base image identity drift")
    missing_controls = [path for path in CONTROL_FILES if not (root / path).is_file()]
    if missing_controls:
        raise GateError("Task 6 control files missing: " + ",".join(missing_controls))

    built = command(
        "docker", "build", "--pull=false", "--network", "none",
        "--build-arg", f"APP_IMAGE={APP_IMAGE}",
        "--build-arg", f"EVAL_IMAGE={EVAL_IMAGE}",
        "-t", TASK_IMAGE, "-f", "Dockerfile", ".",
        cwd=root / "docker/eval-task6",
    )
    print(built.stdout, end="", flush=True)
    if built.returncode:
        raise GateError("Task 6 composite image build failed")
    task_image_id = image_id(TASK_IMAGE)

    evidence_dir = (root / "evidence").resolve()
    test = command(
        "docker", "run", "--rm", "--network", "none",
        "--cpus", "2", "--memory", "4g",
        "-e", "PYTHONPATH=/work",
        "-e", "DJANGO_SETTINGS_MODULE=nextseek_api.eval.task6_settings",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "JAX_ENABLE_X64=1",
        "-e", "OMP_NUM_THREADS=2",
        "-e", "XLA_FLAGS=--xla_cpu_multi_thread_eigen=false",
        "-e", f"PLAN018_TASK6_RESULT=/out/{Path(RESULT).name}",
        "-v", f"{root.resolve()}:/work:ro",
        "-v", f"{delivery.resolve()}:{delivery.resolve()}:ro",
        "-v", f"{evidence_dir}:/out",
        "-w", "/work", TASK_IMAGE,
        "-m", "pytest", "-c", "/dev/null", "--rootdir=/work", "--noconftest", "-q",
        "-p", "no:cacheprovider", f"--junitxml=/out/{Path(JUNIT).name}",
        "nextseek_api/eval/tests/test_v4_9_task6_replay.py",
    )
    print(test.stdout, end="", flush=True)
    if test.returncode:
        raise GateError("Task 6 full replay failed")
    elapsed = time.monotonic() - started
    if elapsed > MAX_WALL_S:
        raise GateError(f"Task 6 exceeded hardware wall cap: {elapsed:.3f}s")

    nodes, counts = junit_counts(root / JUNIT)
    expected_node = (
        "nextseek_api/eval/tests/test_v4_9_task6_replay.py::"
        "test_authenticated_stored_evidence_to_local_routing_chain"
    )
    if nodes != (expected_node,) or counts != {
        "tests": 1, "passed": 1, "failed": 0, "errors": 0,
        "skipped": 0, "xfail": 0, "deselected": 0,
    }:
        raise GateError(f"Task 6 JUnit execution mismatch: nodes={nodes}, counts={counts}")
    replay = json.loads((root / RESULT).read_text())
    if replay.get("gate") != "PASS":
        raise GateError("Task 6 replay result is not PASS")

    evidence = {
        "schema": "plan018-v4-9-task6-evidence/v1",
        "gate": "PASS",
        "delivery": delivery_identity,
        "images": {
            "application": {"tag": APP_IMAGE, "id": APP_IMAGE_ID},
            "eval": {"tag": EVAL_IMAGE, "id": EVAL_IMAGE_ID},
            "task6_composite": {"tag": TASK_IMAGE, "id": task_image_id},
        },
        "control_sha256": {path: sha256(root / path) for path in CONTROL_FILES},
        "artifacts_sha256": {
            RESULT: sha256(root / RESULT),
            JUNIT: sha256(root / JUNIT),
        },
        "execution_counts": counts,
        "wall_s": round(elapsed, 3),
        "wall_cap_s": MAX_WALL_S,
        "network": "none",
        "resource_cap": {"cpus": 2, "memory": "4g"},
        "new_paired_route_execution": False,
        "provider_calls": 0,
        "live_database_or_deployment_used": False,
    }
    (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"Task 6 stored-evidence replay PASS in {elapsed:.3f}s", flush=True)


def validation_errors(root: Path = ROOT, delivery: Path = DELIVERY) -> list[str]:
    errors: list[str] = []
    try:
        delivery_identity = verify_delivery(delivery)
    except (GateError, OSError) as exc:
        return [str(exc)]
    required = (RESULT, JUNIT, EVIDENCE)
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        return ["missing Task 6 artifacts: " + ",".join(missing)]
    try:
        replay = json.loads((root / RESULT).read_text())
        evidence = json.loads((root / EVIDENCE).read_text())
        nodes, counts = junit_counts(root / JUNIT)
    except (GateError, OSError, ValueError, TypeError, ET.ParseError) as exc:
        return [f"malformed Task 6 artifact: {exc}"]

    if evidence.get("gate") != "PASS" or replay.get("gate") != "PASS":
        errors.append("Task 6 gate is not PASS")
    if evidence.get("delivery") != delivery_identity:
        errors.append("Task 6 delivery identity drifted")
    if (
        evidence.get("wall_cap_s") != MAX_WALL_S
        or float(evidence.get("wall_s", MAX_WALL_S + 1)) > MAX_WALL_S
    ):
        errors.append("Task 6 exceeded the hardware wall cap")
    expected_images = {
        "application": {"tag": APP_IMAGE, "id": APP_IMAGE_ID},
        "eval": {"tag": EVAL_IMAGE, "id": EVAL_IMAGE_ID},
    }
    images = evidence.get("images") or {}
    if any(images.get(name) != identity for name, identity in expected_images.items()):
        errors.append("Task 6 base-image evidence drifted")
    composite = images.get("task6_composite") or {}
    if composite.get("tag") != TASK_IMAGE or not str(composite.get("id", "")).startswith("sha256:"):
        errors.append("Task 6 composite-image evidence is malformed")
    try:
        if image_id(APP_IMAGE) != APP_IMAGE_ID or image_id(EVAL_IMAGE) != EVAL_IMAGE_ID:
            errors.append("Task 6 installed base-image identity drifted")
        if composite.get("id") and image_id(TASK_IMAGE) != composite.get("id"):
            errors.append("Task 6 installed composite-image identity drifted")
    except GateError as exc:
        errors.append(str(exc))
    if (
        evidence.get("network") != "none"
        or evidence.get("resource_cap") != {"cpus": 2, "memory": "4g"}
        or evidence.get("new_paired_route_execution") is not False
        or evidence.get("provider_calls") != 0
        or evidence.get("live_database_or_deployment_used") is not False
    ):
        errors.append("Task 6 execution-boundary evidence drifted")
    expected_controls = set(CONTROL_FILES)
    if set(evidence.get("control_sha256") or {}) != expected_controls:
        errors.append("Task 6 control hash key set drifted")
    for relative, expected in (evidence.get("control_sha256") or {}).items():
        if not (root / relative).is_file() or sha256(root / relative) != expected:
            errors.append(f"stale Task 6 control: {relative}")
    if set(evidence.get("artifacts_sha256") or {}) != {RESULT, JUNIT}:
        errors.append("Task 6 artifact hash key set drifted")
    for relative, expected in (evidence.get("artifacts_sha256") or {}).items():
        if not (root / relative).is_file() or sha256(root / relative) != expected:
            errors.append(f"stale Task 6 artifact: {relative}")

    expected_node = (
        "nextseek_api/eval/tests/test_v4_9_task6_replay.py::"
        "test_authenticated_stored_evidence_to_local_routing_chain"
    )
    if nodes != (expected_node,) or counts != evidence.get("execution_counts"):
        errors.append("Task 6 exact execution/JUnit identity drifted")
    if counts != {
        "tests": 1, "passed": 1, "failed": 0, "errors": 0,
        "skipped": 0, "xfail": 0, "deselected": 0,
    }:
        errors.append(f"Task 6 JUnit contains nonexecution: {counts}")
    if replay.get("conservation") != {
        "pairs": 149, "arms": 298, "retained_pairs": 149,
        "excluded_pairs": 0, "pending_pairs": 0, "balanced": True,
    }:
        errors.append("Task 6 pair/arm conservation drifted")
    source = replay.get("source") or {}
    if (
        source.get("archive_sha256") != DELIVERY_FILES["testquestions.zip"]["sha256"]
        or source.get("manifest_sha256") != DELIVERY_FILES["MANIFEST.json"]["sha256"]
        or source.get("artifact_validity_sha256")
        != DELIVERY_FILES["artifact_validity_set3_final.csv"]["sha256"]
        or source.get("member_sha256") != EXPECTED_MEMBER_SHA256
        or source.get("training_corpus_sha256")
        != EXPECTED_MEMBER_SHA256["corpus/corpus.json"]
        or source.get("human_grade_sha256") != {
            "ns": EXPECTED_MEMBER_SHA256[
                "set3_final/hibayes/hibayes_functional_usefulness_human_ns.csv"
            ],
            "cc": EXPECTED_MEMBER_SHA256[
                "set3_final/hibayes/hibayes_functional_usefulness_human_cc.csv"
            ],
        }
    ):
        errors.append("Task 6 exact authenticated fit source drifted")
    judgments = replay.get("stored_judgments") or {}
    if (
        judgments.get("eligible_arms") != 274
        or judgments.get("ineligible_arms") != 24
        or judgments.get("stored_attempts") != 822
        or judgments.get("calls_per_eligible_arm") != 3
        or judgments.get("provider_calls") != 0
        or judgments.get("historical_provider_judgments_claimed") is not False
        or judgments.get("source_kind")
        != "authenticated_human_grade_acceptance_oracle"
        or not is_sha256(judgments.get("attempt_manifest_sha256"))
        or not is_sha256(judgments.get("aggregate_manifest_sha256"))
    ):
        errors.append("Task 6 stored-judgment conservation/provenance drifted")
    fit = replay.get("fit") or {}
    if (
        fit.get("mode") != "initial_human_grade"
        or fit.get("quality_mcmc") is not True
        or fit.get("latency_mcmc") is not False
        or fit.get("diagnostics_ok") is not True
        or set(fit.get("candidate_status") or {})
        != {"graph_traversal", "unsupported", "sample_search"}
        or not {"graph_traversal", "unsupported"}.issubset(
            set(fit.get("activated_families") or [])
        )
        or "sample_search" in set(fit.get("activated_families") or [])
    ):
        errors.append("Task 6 fit authority/decision evidence drifted")
    publication = replay.get("publication") or {}
    activation = replay.get("activation") or {}
    if (
        publication.get("registered_existing_transferred_run") is not True
        or publication.get("publication_authority")
        != "provisional_initial_human_grade"
        or not is_sha256(publication.get("paired_content_hash"))
        or not is_sha256(publication.get("generation_hash"))
        or activation.get("environment") != "isolated_in_memory_sqlite"
        or activation.get("active_generation_hash") != publication.get("generation_hash")
    ):
        errors.append("Task 6 publication/local-activation evidence drifted")
    if replay.get("routing") != {
        "graph_traversal": "nextseek_query",
        "unsupported": "container_cc",
        "sample_search": "legacy_fallback",
    }:
        errors.append("Task 6 activated routing/fallback result drifted")
    effects = replay.get("external_effects") or {}
    if (
        effects.get("new_paired_route_execution") is not False
        or effects.get("provider_calls") != 0
        or effects.get("live_database") is not False
        or effects.get("deployment") is not False
        or effects.get("production_enablement") is not False
    ):
        errors.append("Task 6 external-effects attestation drifted")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "validate"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--delivery", type=Path, default=DELIVERY)
    args = parser.parse_args()
    if args.action == "run":
        run(args.root.resolve(), args.delivery.resolve())
        return 0
    errors = validation_errors(args.root.resolve(), args.delivery.resolve())
    print("Task 6 evidence " + ("PASS" if not errors else "FAIL"))
    for error in errors:
        print("- " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
