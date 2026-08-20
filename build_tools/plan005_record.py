"""Shell-free Plan 005 command evidence recorder."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from build_tools.plan005_closeout import (
    COMMAND_TIMEOUT_SECONDS,
    EVIDENCE_PARENT,
    IMMUTABLE_NEXTSEEK_IMAGE,
    SEQUENCE_BUDGET_SECONDS,
    artifact_namespace,
)

SECRET_PATTERN = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|passwd|credential|aws_secret)",
    re.IGNORECASE,
)
IMAGE_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DOCKER_VALUE_FLAGS = {
    "-e",
    "--env",
    "-v",
    "--volume",
    "-w",
    "--workdir",
    "-u",
    "--user",
    "--name",
    "--entrypoint",
    "--network",
    "--hostname",
    "--label",
    "-l",
    "--env-file",
    "--mount",
    "--tmpfs",
    "--cidfile",
    "--runtime",
    "--platform",
    "--pull",
    "-m",
    "--memory",
    "--cpus",
    "--add-host",
}


class RecordError(ValueError):
    """Raised when a command cannot be recorded under Plan 005 rules."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_immutable_image(image: str) -> bool:
    return bool(IMAGE_SHA_RE.fullmatch(image))


def refuse_secret_bearing(argv: list[str], env: Mapping[str, str]) -> None:
    for token in argv:
        if SECRET_PATTERN.search(token):
            raise RecordError(f"secret-bearing argv token refused: {token!r}")
    for key, value in env.items():
        if SECRET_PATTERN.search(key) or SECRET_PATTERN.search(value):
            raise RecordError(f"secret-bearing environment refused: {key}")


def non_secret_env_keys(env: Mapping[str, str]) -> list[str]:
    return sorted(key for key in env if not SECRET_PATTERN.search(key))


def parse_docker_image(argv: list[str]) -> str | None:
    if len(argv) < 2 or argv[0] != "docker" or argv[1] != "run":
        return None
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in DOCKER_VALUE_FLAGS or (
            token.startswith("-") and not token.startswith("--") and token not in {"-d", "-i", "-t", "-it"}
            and len(token) == 2
        ):
            if token in {"-d", "-i", "-t"}:
                index += 1
                continue
            if token in DOCKER_VALUE_FLAGS:
                index += 2
                continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def parse_docker_volumes(argv: list[str]) -> list[tuple[str, str, str]]:
    volumes: list[tuple[str, str, str]] = []
    if len(argv) < 2 or argv[0] != "docker" or argv[1] != "run":
        return volumes
    index = 2
    while index < len(argv):
        token = argv[index]
        spec = None
        if token in {"-v", "--volume"} and index + 1 < len(argv):
            spec = argv[index + 1]
            index += 2
        elif token.startswith("--volume="):
            spec = token.split("=", 1)[1]
            index += 1
        else:
            index += 1
            continue
        parts = spec.split(":")
        if len(parts) < 2:
            raise RecordError(f"malformed volume spec: {spec!r}")
        host, container = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 else "rw"
        volumes.append((host, container, mode))
    return volumes


def docker_network_is_none(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return "--network none" in joined or "--network=none" in joined


def refuse_forbidden_pytest(argv: list[str]) -> None:
    joined = " ".join(argv)
    if "pytest-cov" in joined or any(tok == "--cov" or tok.startswith("--cov=") for tok in argv):
        raise RecordError("pytest-cov is forbidden")
    if "pytest-xdist" in joined or "-n" in argv:
        raise RecordError("xdist is forbidden")


def refuse_mutable_image(argv: list[str]) -> None:
    image = parse_docker_image(argv)
    if image is None:
        for token in argv:
            if token.endswith(":latest") or (
                not token.startswith("sha256:")
                and re.search(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+$", token)
                and "/" in token or token.startswith("nextseek-")
            ):
                if ":" in token and not token.startswith("sha256:") and token[0].isalpha():
                    if any(tag in token for tag in (":latest", ":dev", "nextseek-nextseek:")):
                        raise RecordError(f"mutable image tag refused: {token}")
        return
    if not is_immutable_image(image):
        raise RecordError(f"mutable image tag refused: {image}")


def refuse_writable_mounts(
    argv: list[str],
    *,
    evidence_root: Path,
    writable_output: Path,
) -> None:
    volumes = parse_docker_volumes(argv)
    evidence_root = evidence_root.resolve()
    writable_output = writable_output.resolve()
    for host, container, mode in volumes:
        host_path = Path(host).resolve()
        writable = mode != "ro"
        if not writable:
            continue
        if host_path == evidence_root:
            raise RecordError("writable evidence-root mount refused")
        if host_path != writable_output and (
            host_path == evidence_root / "artifacts"
            or (evidence_root / "artifacts") in host_path.parents
            or host_path.parent == evidence_root / "artifacts"
        ):
            raise RecordError(
                f"writable prior-artifact mount refused: {host}:{container}:{mode}"
            )
        if container == "/evidence" and host_path != writable_output:
            raise RecordError(
                "writable /evidence must be this command's artifacts namespace: "
                f"{writable_output} != {host_path}"
            )


def git_snapshot(repo_root: Path) -> dict[str, Any]:
    def _run(args: list[str]) -> str:
        completed = subprocess.run(
            args,
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise RecordError(
                f"git snapshot failed: {args}: {completed.stderr.strip()}"
            )
        return completed.stdout

    branch = _run(["git", "branch", "--show-current"]).strip()
    head = _run(["git", "rev-parse", "HEAD"]).strip()
    tree = _run(["git", "rev-parse", "HEAD^{tree}"]).strip()
    porcelain = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"]
    )
    return {
        "branch": branch,
        "head": head,
        "tree": tree,
        "porcelain": porcelain,
    }


def porcelain_paths(porcelain: str) -> list[str]:
    paths: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        rest = line[3:] if len(line) > 3 else line
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        paths.append(rest)
    return paths


def refuse_dirty_head(
    snapshot: dict[str, Any],
    *,
    declared_repo_outputs: tuple[Path, ...],
    repo_root: Path,
) -> None:
    paths = porcelain_paths(snapshot["porcelain"])
    if not paths:
        return
    if not declared_repo_outputs:
        raise RecordError("refuse dirty HEAD unless declared repository output")
    allowed = [path.resolve() for path in declared_repo_outputs]
    for rel in paths:
        candidate = (repo_root / rel).resolve()
        if any(
            candidate == item or item in candidate.parents or candidate in item.parents
            for item in allowed
        ):
            continue
        raise RecordError(f"dirty path outside declared repository output: {rel}")


def walk_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        manifest[rel] = sha256_file(path)
    return manifest


def refuse_evidence_root_drift(
    before: dict[str, str],
    after: dict[str, str],
    *,
    record_rel: str,
    artifact_rel: str,
) -> None:
    allowed_prefixes = (record_rel.rstrip("/") + "/", artifact_rel.rstrip("/") + "/")
    for rel, digest in after.items():
        if rel in before:
            if before[rel] != digest:
                raise RecordError(f"prior evidence mutated: {rel}")
            continue
        if not rel.startswith(allowed_prefixes):
            raise RecordError(f"undeclared evidence-root write: {rel}")
    for rel in before:
        if rel not in after:
            raise RecordError(f"prior evidence removed: {rel}")


def remaining_sequence_budget(
    evidence_root: Path,
    *,
    sequence_budget_seconds: int,
    now: float | None = None,
) -> float:
    records_dir = evidence_root / "records"
    starts: list[float] = []
    if records_dir.is_dir():
        for record_file in records_dir.glob("*/record.json"):
            payload = json.loads(record_file.read_text(encoding="utf-8"))
            start = payload.get("start_monotonic")
            if isinstance(start, (int, float)):
                starts.append(float(start))
    clock = time.monotonic() if now is None else now
    if not starts:
        return float(sequence_budget_seconds)
    elapsed = clock - min(starts)
    return float(sequence_budget_seconds) - elapsed


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def record_command(
    *,
    evidence_root: Path,
    name: str,
    argv: list[str],
    writable_output: Path,
    repo_root: Path,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    sequence_budget_seconds: int = SEQUENCE_BUDGET_SECONDS,
    command_timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
    declared_repo_output: Path | Sequence[Path] | None = None,
    ensure_declared_repo_output: bool = False,
    declared_source_manifest: dict[str, str] | None = None,
    declared_generated_target_manifest: dict[str, str] | None = None,
    runner=None,
) -> dict[str, Any]:
    """Launch argv without a shell and persist a unique evidence record."""
    if not NAME_RE.fullmatch(name):
        raise RecordError(f"invalid command name: {name!r}")
    if command_timeout_seconds > COMMAND_TIMEOUT_SECONDS:
        raise RecordError("timeout inflation forbidden")
    if sequence_budget_seconds > SEQUENCE_BUDGET_SECONDS:
        raise RecordError("sequence budget inflation forbidden")
    evidence_root = evidence_root.resolve()
    writable_output = writable_output.resolve()
    repo_root = repo_root.resolve()
    cwd = (cwd or repo_root).resolve()
    child_env = dict(env if env is not None else os.environ)
    refuse_secret_bearing(argv, child_env)
    refuse_forbidden_pytest(argv)
    refuse_mutable_image(argv)
    if argv and argv[0] == "docker" and argv[1:2] == ["run"] and not docker_network_is_none(argv):
        raise RecordError("docker run must use --network none")

    expected_ns = artifact_namespace(name)
    expected_writable = evidence_root / "artifacts" / expected_ns
    if writable_output != expected_writable:
        raise RecordError(
            f"writable-output must be {expected_writable}, got {writable_output}"
        )
    try:
        writable_output.relative_to(Path(EVIDENCE_PARENT))
    except ValueError:
        try:
            writable_output.relative_to(evidence_root)
        except ValueError as exc:
            raise RecordError("repository-local evidence output refused") from exc
    if repo_root in writable_output.parents or writable_output == repo_root:
        raise RecordError("repository-local evidence output refused")

    refuse_writable_mounts(
        argv,
        evidence_root=evidence_root,
        writable_output=writable_output,
    )

    record_dir = evidence_root / "records" / name
    if record_dir.exists() or writable_output.exists():
        raise RecordError("refuse overwrite/reuse of record or artifact namespace")

    remaining = remaining_sequence_budget(
        evidence_root, sequence_budget_seconds=sequence_budget_seconds
    )
    if remaining <= 0:
        raise RecordError("sequence wall-clock budget exhausted")
    timeout = min(float(command_timeout_seconds), remaining)

    if declared_repo_output is None:
        declared_outputs: tuple[Path, ...] = ()
    elif isinstance(declared_repo_output, (list, tuple)):
        declared_outputs = tuple(Path(p) for p in declared_repo_output)
    else:
        declared_outputs = (Path(declared_repo_output),)

    pre = git_snapshot(repo_root)
    refuse_dirty_head(
        pre, declared_repo_outputs=declared_outputs, repo_root=repo_root
    )
    before_manifest = walk_manifest(evidence_root)

    if ensure_declared_repo_output:
        if not declared_outputs:
            raise RecordError("ensure-declared-repo-output requires --declared-repo-output")
        for path in declared_outputs:
            path.mkdir(parents=True, exist_ok=True)

    record_dir.mkdir(parents=True)
    writable_output.mkdir(parents=True)

    start_mono = time.monotonic()
    start_time = utc_now()
    run = runner or subprocess.run
    try:
        completed = run(
            argv,
            cwd=str(cwd),
            env=dict(child_env),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        exit_code = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = (exc.stderr or b"") + f"timed out after {timeout}s\n".encode("utf-8")
        exit_code = 124
    end_mono = time.monotonic()
    end_time = utc_now()

    (record_dir / "stdout.bin").write_bytes(stdout)
    (record_dir / "stderr.bin").write_bytes(stderr)
    post = git_snapshot(repo_root)
    if post["head"] != pre["head"] or post["tree"] != pre["tree"]:
        raise RecordError("HEAD/tree changed during recorded command")
    if not declared_outputs and post["porcelain"] != pre["porcelain"]:
        raise RecordError("HEAD/tree/porcelain changed without declared repository output")
    after_manifest = walk_manifest(evidence_root)
    refuse_evidence_root_drift(
        before_manifest,
        after_manifest,
        record_rel=f"records/{name}",
        artifact_rel=f"artifacts/{expected_ns}",
    )

    generated_manifest = declared_generated_target_manifest
    if generated_manifest is None and declared_outputs:
        generated_manifest = {}
        for path in declared_outputs:
            if not path.exists():
                continue
            for file_path in sorted(path.rglob("*")):
                if file_path.is_file():
                    rel = file_path.relative_to(repo_root).as_posix()
                    generated_manifest[rel] = sha256_bytes(file_path.read_bytes())
    if generated_manifest is None:
        generated_manifest = {}

    payload = {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "env_keys": non_secret_env_keys(child_env),
        "start_time": start_time,
        "end_time": end_time,
        "start_monotonic": start_mono,
        "end_monotonic": end_mono,
        "elapsed_seconds": end_mono - start_mono,
        "exit_code": exit_code,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_sha256": sha256_bytes(stderr),
        "pre": pre,
        "post": post,
        "declared_source_manifest": declared_source_manifest or {},
        "declared_generated_target_manifest": generated_manifest,
        "evidence_root_before": before_manifest,
        "evidence_root_after": after_manifest,
        "command_timeout_seconds": command_timeout_seconds,
        "sequence_budget_seconds": sequence_budget_seconds,
        "timeout_enforced_seconds": timeout,
        "image": parse_docker_image(argv) or IMMUTABLE_NEXTSEEK_IMAGE,
        "writable_output": str(writable_output),
        "tool_head": pre["head"],
        "tool_tree": pre["tree"],
    }
    _write_json(record_dir / "record.json", payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a Plan 005 command without a shell.")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--writable-output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("/home/taishajo/work/NExtSEEK-plan005"))
    parser.add_argument("--cwd", type=Path, default=None)
    parser.add_argument("--sequence-budget-seconds", type=int, default=SEQUENCE_BUDGET_SECONDS)
    parser.add_argument("--command-timeout-seconds", type=int, default=COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--declared-repo-output", type=Path, action="append", default=None)
    parser.add_argument("--ensure-declared-repo-output", action="store_true")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = list(args.argv)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("plan005_record: missing command argv after --", file=sys.stderr)
        return 2
    try:
        payload = record_command(
            evidence_root=args.evidence_root,
            name=args.name,
            argv=command,
            writable_output=args.writable_output,
            repo_root=args.repo_root,
            cwd=args.cwd,
            sequence_budget_seconds=args.sequence_budget_seconds,
            command_timeout_seconds=args.command_timeout_seconds,
            declared_repo_output=args.declared_repo_output,
            ensure_declared_repo_output=args.ensure_declared_repo_output,
        )
    except RecordError as exc:
        print(f"plan005_record failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"name": payload["name"], "exit_code": payload["exit_code"]}))
    return 0 if payload["exit_code"] == 0 else payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
