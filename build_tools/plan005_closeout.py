"""Plan 005 16-row closeout protocol metadata (Task 12).

Live preflight/finalize/verify against a completed 16-lane evidence tree is
Task 13 / final-gate. This module owns the ordered record IDs, argv templates,
mount contracts, and fixture-tested set-equality checks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

IMMUTABLE_NEXTSEEK_IMAGE = (
    "sha256:879406139db3581c6f1b040a5bdcef40385a62780af01e71d2766003e3745a81"
)
IMMUTABLE_VALIDATOR_IMAGE = (
    "sha256:6f4f309cfe24f24047590251ba0ad34ff0c0ed7868b58b080f97b44ed800654c"
)
PLAN005_BASE_COMMIT = "a9d69522bc5371365331a93aa2f048f28324fa1c"
SEQUENCE_BUDGET_SECONDS = 3600
COMMAND_TIMEOUT_SECONDS = 600
COVERAGE_MIN_TOTAL = 95
EVIDENCE_PARENT = "/home/taishajo/work/state/plan005/execution"
REPO_ROOT_TEMPLATE = "/home/taishajo/work/NExtSEEK-plan005"
THREE_PYTEST_IGNORES: tuple[str, ...] = (
    "nextseek_api/cc_assistant/tests/test_cc_realstack.py",
    "nextseek_api/cc_assistant/tests/test_within_chat_db.py",
    "nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py",
)

PROTOCOL_RECORD_IDS: tuple[str, ...] = (
    "01-baseline",
    "02-export-check",
    "03-surfaces-check",
    "04-baml-setup",
    "05-future-op",
    "06-audit-a",
    "07-assistant-route",
    "08-build-tools",
    "09-compose-quiet",
    "10-compose-json",
    "11-plugin-validator",
    "12-coverage-run",
    "13-coverage-json",
    "14-coverage-report",
    "15-coverage-config",
    "16-final-gate",
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "plan005-closeout.schema.json"


class ProtocolError(ValueError):
    """Raised when the 16-row protocol is extra, missing, renamed, or duplicated."""


def artifact_namespace(record_id: str) -> str:
    prefix, sep, rest = record_id.partition("-")
    if not sep or len(prefix) != 2 or not prefix.isdigit() or not rest:
        raise ProtocolError(f"invalid record id: {record_id!r}")
    return rest


def _row(
    record_id: str,
    argv: list[str],
    *,
    image: str = IMMUTABLE_NEXTSEEK_IMAGE,
    read_only: tuple[str, ...] = (),
    evidence_mount: str = "/evidence",
    repository_mount: str = "/repo",
) -> dict[str, Any]:
    return {
        "id": record_id,
        "argv_template": argv,
        "image": image,
        "network": "none",
        "declared_output_namespace": f"artifacts/{artifact_namespace(record_id)}",
        "read_only_producer_inputs": list(read_only),
        "repository_mount": repository_mount,
        "evidence_mount": evidence_mount,
    }


def _docker_python(*tail: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "XDG_CACHE_HOME=/tmp/plan005-cache",
        "-v",
        "{repo}:/repo:ro",
        "-w",
        "/repo",
        "{image}",
        "/app/.venv/bin/python",
        *tail,
    ]


def _coverage_followon(*tail: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "COVERAGE_FILE=/coverage-input/.coverage",
        "-v",
        "{repo}:/repo:ro",
        "-w",
        "/repo",
        "-v",
        "{evidence_root}/artifacts/coverage-run:/coverage-input:ro",
        "-v",
        "{writable}:/evidence",
        "{image}",
        "/app/.venv/bin/python",
        *tail,
    ]


def protocol_rows() -> list[dict[str, Any]]:
    """Return the fixed 16-row execution protocol (control-flow metadata only)."""
    ignores: list[str] = []
    for path in THREE_PYTEST_IGNORES:
        ignores.extend(["--ignore", path])
    return [
        _row(
            "01-baseline",
            [
                "python3",
                "-m",
                "build_tools.plan005_baseline",
                "--repo-root",
                "{repo}",
                "--base",
                PLAN005_BASE_COMMIT,
                "--output",
                f"{EVIDENCE_PARENT}/base-a9d69522/{{candidate}}",
                "--image",
                "{image}",
            ],
            repository_mount="{repo}",
            evidence_mount=f"{EVIDENCE_PARENT}/base-a9d69522/{{candidate}}",
        ),
        _row(
            "02-export-check",
            _docker_python(
                "-m",
                "nextseek_api.cc_assistant.op_registry.export",
                "--check",
                "--root",
                "/repo",
            ),
        ),
        _row(
            "03-surfaces-check",
            _docker_python(
                "-m",
                "build_tools.gen_op_surfaces",
                "--check",
                "--root",
                "/repo",
            ),
        ),
        _row(
            "04-baml-setup",
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "XDG_CACHE_HOME=/tmp/plan005-cache",
                "-v",
                "{repo}:/repo:ro",
                "-v",
                "{repo}/dmac_assistant/src/dmac_assistant/router:"
                "/repo/dmac_assistant/src/dmac_assistant/router",
                "-v",
                "{repo}/dmac_assistant/tools/e2e:"
                "/repo/dmac_assistant/tools/e2e",
                "-w",
                "/repo",
                "{image}",
                "uv",
                "run",
                "--project",
                "/app",
                "--no-sync",
                "baml-cli",
                "generate",
                "--from",
                "dmac_assistant/baml_src",
                "--no-version-check",
            ],
        ),
        _row(
            "05-future-op",
            _docker_python(
                "-m",
                "pytest",
                "nextseek_api/cc_assistant/tests/test_future_op_dropin.py",
                "--junitxml=/evidence/future-op.junit.xml",
                "-p",
                "no:cacheprovider",
                "-q",
            ),
        ),
        _row(
            "06-audit-a",
            _docker_python(
                "-m",
                "pytest",
                "nextseek_api/cc_assistant/tests/test_op_registry_audit.py",
                "--junitxml=/evidence/audit-a.junit.xml",
                "-p",
                "no:cacheprovider",
                "-q",
            ),
        ),
        _row(
            "07-assistant-route",
            _docker_python(
                "-m",
                "pytest",
                "nextseek_api/assistant/tests/test_route_capabilities.py",
                "--junitxml=/evidence/assistant-route.junit.xml",
                "-p",
                "no:cacheprovider",
                "-q",
            ),
        ),
        _row(
            "08-build-tools",
            _docker_python(
                "-m",
                "pytest",
                "build_tools/tests",
                "--junitxml=/evidence/build-tools.junit.xml",
                "-p",
                "no:cacheprovider",
                "-q",
            ),
        ),
        _row(
            "09-compose-quiet",
            [
                "docker",
                "compose",
                "-f",
                "{repo}/docker-compose.yml",
                "config",
                "--no-env-resolution",
                "--quiet",
            ],
            image="",
        ),
        _row(
            "10-compose-json",
            [
                "docker",
                "compose",
                "-f",
                "{repo}/docker-compose.yml",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            image="",
        ),
        _row(
            "11-plugin-validator",
            [
                "python3",
                "-m",
                "build_tools.plan005_validate_plugins",
                "--repo-root",
                "{repo}",
                "--validator-image",
                IMMUTABLE_VALIDATOR_IMAGE,
            ],
            image=IMMUTABLE_VALIDATOR_IMAGE,
        ),
        _row(
            "12-coverage-run",
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "DJANGO_SETTINGS_MODULE=dmac.test_settings",
                "-e",
                "PYTHONPATH=/repo:/repo/dmac_assistant/src:/repo/chat_nextseek/src",
                "-e",
                "COVERAGE_FILE=/evidence/.coverage",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "GIT_CONFIG_COUNT=2",
                "-e",
                "GIT_CONFIG_KEY_0=safe.directory",
                "-e",
                "GIT_CONFIG_VALUE_0=/repo",
                "-e",
                "GIT_CONFIG_KEY_1=safe.directory",
                "-e",
                "GIT_CONFIG_VALUE_1={repo}",
                "-v",
                "{repo}:/repo:ro",
                "-v",
                "/home/taishajo/work/NExtSEEK/.git:/home/taishajo/work/NExtSEEK/.git:ro",
                "-w",
                "/repo",
                "-v",
                "{writable}:/evidence",
                "{image}",
                "/app/.venv/bin/python",
                "-m",
                "coverage",
                "run",
                "--rcfile=/dev/null",
                "--branch",
                "--source=nextseek_api.cc_assistant",
                "-m",
                "pytest",
                "nextseek_api/cc_assistant/tests",
                *ignores,
                "--junitxml=/evidence/cc-assistant.junit.xml",
                "-p",
                "no:cacheprovider",
                "-q",
            ],
        ),
        _row(
            "13-coverage-json",
            _coverage_followon(
                "-m",
                "coverage",
                "json",
                "--rcfile=/dev/null",
                "-o",
                "/evidence/cc-assistant-coverage.json",
            ),
            read_only=("artifacts/coverage-run",),
        ),
        _row(
            "14-coverage-report",
            _coverage_followon(
                "-m",
                "coverage",
                "report",
                "--rcfile=/dev/null",
                "--fail-under=95",
            ),
            read_only=("artifacts/coverage-run",),
        ),
        _row(
            "15-coverage-config",
            _coverage_followon(
                "-m",
                "coverage",
                "debug",
                "--rcfile=/dev/null",
                "config",
            ),
            read_only=("artifacts/coverage-run",),
        ),
        _row(
            "16-final-gate",
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "PYTHONPATH={repo}",
                "-e",
                "GIT_CONFIG_COUNT=1",
                "-e",
                "GIT_CONFIG_KEY_0=safe.directory",
                "-e",
                "GIT_CONFIG_VALUE_0={repo}",
                "-v",
                "{repo}:{repo}:ro",
                "-v",
                "/home/taishajo/work/NExtSEEK/.git:/home/taishajo/work/NExtSEEK/.git:ro",
                "-w",
                "{repo}",
                "-v",
                "{evidence_root}:/all-evidence:ro",
                "-v",
                "{writable}:/evidence",
                "-v",
                f"{EVIDENCE_PARENT}/base-a9d69522/{{candidate}}:/baseline:ro",
                "{image}",
                "/app/.venv/bin/python",
                "-m",
                "build_tools.plan005_gate",
                "--coverage-json",
                "/all-evidence/artifacts/coverage-json/cc-assistant-coverage.json",
                "--junit",
                "/all-evidence/artifacts/coverage-run/cc-assistant.junit.xml",
                "--junit",
                "/all-evidence/artifacts/assistant-route/assistant-route.junit.xml",
                "--junit",
                "/all-evidence/artifacts/build-tools/build-tools.junit.xml",
                "--junit",
                "/all-evidence/artifacts/audit-a/audit-a.junit.xml",
                "--junit",
                "/all-evidence/artifacts/future-op/future-op.junit.xml",
                "--baseline-junit",
                "/baseline/base-cc-assistant.junit.xml",
                "--source-root",
                "{repo}/nextseek_api/cc_assistant",
                "--base",
                PLAN005_BASE_COMMIT,
                "--min-total",
                "95",
            ],
            read_only=(
                "artifacts/coverage-json",
                "artifacts/coverage-run",
                "artifacts/assistant-route",
                "artifacts/build-tools",
                "artifacts/audit-a",
                "artifacts/future-op",
            ),
            repository_mount="{repo}",
        ),
    ]


def protocol_manifest() -> dict[str, Any]:
    rows = protocol_rows()
    return {"record_ids": list(PROTOCOL_RECORD_IDS), "rows": rows}


def validate_protocol_rows(rows: list[dict[str, Any]]) -> None:
    """Require exact ordered set equality with the 16 locked record IDs."""
    if not isinstance(rows, list):
        raise ProtocolError("protocol rows must be a list")
    ids = [row.get("id") for row in rows]
    if len(ids) != len(PROTOCOL_RECORD_IDS):
        raise ProtocolError(
            f"protocol row count {len(ids)} != {len(PROTOCOL_RECORD_IDS)}"
        )
    if len(ids) != len(set(ids)):
        raise ProtocolError("duplicate protocol record ids")
    expected = set(PROTOCOL_RECORD_IDS)
    got = set(ids)
    extra = sorted(got - expected)
    missing = sorted(expected - got)
    renamed = []
    if extra or missing:
        raise ProtocolError(
            f"protocol ids mismatch extra={extra} missing={missing} renamed={renamed}"
        )
    if tuple(ids) != PROTOCOL_RECORD_IDS:
        raise ProtocolError("protocol record ids are out of order; 16-final-gate must be last")
    if ids[-1] != "16-final-gate":
        raise ProtocolError("16-final-gate must be last")
    for row in rows:
        _validate_row_contract(row)


def _validate_row_contract(row: dict[str, Any]) -> None:
    argv = row.get("argv_template") or []
    joined = " ".join(argv)
    network = row.get("network")
    if network != "none":
        raise ProtocolError(f"{row.get('id')}: network must be none")
    if "docker" in argv and "run" in argv:
        if "--network" not in argv or "none" not in argv:
            raise ProtocolError(f"{row.get('id')}: docker run must set --network none")
        image = row.get("image") or ""
        if not image.startswith("sha256:") or len(image) != 7 + 64:
            raise ProtocolError(f"{row.get('id')}: docker run image must be immutable sha256")
        if ":latest" in joined or not image.startswith("sha256:"):
            raise ProtocolError(f"{row.get('id')}: mutable image tag forbidden")
    if "pytest-cov" in joined or "--cov" in argv:
        raise ProtocolError(f"{row.get('id')}: pytest-cov is forbidden")
    if "-n" in argv or "xdist" in joined or "pytest-xdist" in joined:
        raise ProtocolError(f"{row.get('id')}: xdist is forbidden")
    if row["id"] == "12-coverage-run":
        if "--branch" not in argv:
            raise ProtocolError("12-coverage-run must enable branch coverage")
        if "--source=nextseek_api.cc_assistant" not in argv:
            raise ProtocolError("12-coverage-run must not narrow coverage source")
        ignore_args = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--ignore"]
        if tuple(ignore_args) != THREE_PYTEST_IGNORES:
            raise ProtocolError("12-coverage-run extra or missing pytest ignores")
        if any("omit" in tok for tok in argv):
            raise ProtocolError("12-coverage-run must not omit source")
    if row["id"] == "14-coverage-report":
        if "--fail-under=95" not in argv:
            raise ProtocolError("14-coverage-report must keep fail-under=95")
    if row["id"] == "16-final-gate":
        if "--min-total" not in argv:
            raise ProtocolError("16-final-gate missing --min-total")
        idx = argv.index("--min-total")
        if argv[idx + 1] != "95":
            raise ProtocolError("16-final-gate must not reduce the 95 percent threshold")
    ns = row.get("declared_output_namespace", "")
    if ns.startswith("/") or "NExtSEEK-plan005" in ns:
        raise ProtocolError(f"{row.get('id')}: evidence output must not be repository-local")
    timeout_tokens = [tok for tok in argv if tok.startswith("--timeout")]
    for tok in timeout_tokens:
        if tok.startswith("--timeout="):
            value = int(tok.split("=", 1)[1])
        else:
            continue
        if value > COMMAND_TIMEOUT_SECONDS:
            raise ProtocolError(f"{row.get('id')}: timeout inflation forbidden")


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def protocol_to_schema_instance() -> dict[str, Any]:
    return protocol_manifest()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan 005 closeout protocol metadata.")
    parser.add_argument(
        "stage",
        choices=("protocol", "preflight", "finalize", "verify"),
        help="protocol dumps/checks the 16-row manifest. Other stages are Task 13.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.stage != "protocol":
        print(
            f"plan005_closeout: stage {args.stage!r} is reserved for the Task 13 "
            "16-lane closeout sequence and is not executed in Task 12",
            file=sys.stderr,
        )
        return 2
    manifest = protocol_manifest()
    validate_protocol_rows(manifest["rows"])
    if args.json:
        sys.stdout.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    else:
        print(" ".join(PROTOCOL_RECORD_IDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
