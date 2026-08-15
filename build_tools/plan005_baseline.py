"""Materialize exact Git-base blobs outside the repo and record the baseline lane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from build_tools.plan005_closeout import (
    IMMUTABLE_NEXTSEEK_IMAGE,
    PINNED_PAIRED_ZIP_VOLUME,
    PLAN005_BASE_COMMIT,
    THREE_PYTEST_IGNORES,
)
from build_tools.plan005_record import RecordError, record_command

BAML_SRC_GLOB = "dmac_assistant/baml_src"
BAML_CLIENT_GLOB = "dmac_assistant/src/dmac_assistant/router/baml_client"


class BaselineError(ValueError):
    """Raised when Git-base materialization or the baseline lane fails."""


def git_ls_tree(repo_root: Path, rev: str, git_runner=None) -> list[tuple[str, str, str, str]]:
    """Return (mode, type, sha, path) rows from `git ls-tree -r`."""
    runner = git_runner or subprocess.run
    completed = runner(
        ["git", "-C", str(repo_root), "ls-tree", "-r", rev],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise BaselineError(f"git ls-tree failed: {completed.stderr}")
    rows: list[tuple[str, str, str, str]] = []
    for line in completed.stdout.splitlines():
        meta, path = line.split("\t", 1)
        mode, obj_type, sha = meta.split()
        rows.append((mode, obj_type, sha, path))
    return rows


def git_cat_file(repo_root: Path, sha: str, git_runner=None) -> bytes:
    runner = git_runner or subprocess.run
    completed = runner(
        ["git", "-C", str(repo_root), "cat-file", "blob", sha],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BaselineError(f"git cat-file failed for {sha}")
    return completed.stdout


def git_rev_parse(repo_root: Path, spec: str, git_runner=None) -> str:
    runner = git_runner or subprocess.run
    completed = runner(
        ["git", "-C", str(repo_root), "rev-parse", spec],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise BaselineError(f"git rev-parse failed for {spec}: {completed.stderr}")
    return completed.stdout.strip()


def materialize_base_tree(
    *,
    repo_root: Path,
    base: str,
    dest: Path,
    git_runner=None,
) -> dict[str, str]:
    """Extract every base blob to dest and verify path/type/blob identity."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    rows = git_ls_tree(repo_root, base, git_runner=git_runner)
    blob_manifest: dict[str, str] = {}
    for mode, obj_type, sha, path in rows:
        if obj_type == "commit":
            raise BaselineError(f"gitlink/submodule refused: {path}")
        if "symlink" in mode or mode.startswith("120"):
            raise BaselineError(f"symlink refused: {path}")
        if obj_type != "blob":
            raise BaselineError(f"unexpected ls-tree type {obj_type} at {path}")
        if path.startswith("/") or ".." in Path(path).parts:
            raise BaselineError(f"path traversal refused: {path}")
        target = dest / path
        try:
            target.resolve().relative_to(dest)
        except ValueError as exc:
            raise BaselineError(f"path traversal refused: {path}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        data = git_cat_file(repo_root, sha, git_runner=git_runner)
        digest = hashlib.sha1(f"blob {len(data)}\0".encode("utf-8") + data).hexdigest()
        if digest != sha:
            raise BaselineError(f"wrong blob at {path}: {digest} != {sha}")
        target.write_bytes(data)
        if mode not in {"100644", "100755"}:
            raise BaselineError(f"unsupported blob mode {mode} at {path}")
        target.chmod(0o755 if mode == "100755" else 0o644)
        blob_manifest[path] = sha
    extracted = {
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*")
        if p.is_file()
    }
    expected = set(blob_manifest)
    extra = sorted(extracted - expected)
    missing = sorted(expected - extracted)
    if extra:
        raise BaselineError(f"extra file in materialized tree: {extra}")
    if missing:
        raise BaselineError(f"omitted blob: {missing}")
    return blob_manifest


BASELINE_JUNIT_NAME = "base-cc-assistant.junit.xml"


def _publish_baseline_junit(*, pytest_writable: Path, output: Path) -> None:
    src = pytest_writable / BASELINE_JUNIT_NAME
    if not src.is_file():
        raise BaselineError(
            "baseline pytest junit missing from artifacts/baseline-pytest: "
            f"{src}"
        )
    dest = output / BASELINE_JUNIT_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def hashed_file_manifest(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def sorted_file_manifest(root: Path) -> list[str]:
    return sorted(hashed_file_manifest(root))


def baseline_identities(*, repo_root: Path, base: str, git_runner=None) -> dict[str, str]:
    return {
        "tool_head": git_rev_parse(repo_root, "HEAD", git_runner=git_runner),
        "tool_tree": git_rev_parse(repo_root, "HEAD^{tree}", git_runner=git_runner),
        "subject_base": git_rev_parse(repo_root, base, git_runner=git_runner),
        "subject_tree": git_rev_parse(repo_root, f"{base}^{{tree}}", git_runner=git_runner),
    }


def materialize_base_index(*, repo_root: Path, base: str, output: Path) -> Path:
    """Write an external index for *base* without touching shared Git metadata."""
    index_path = output / "base.index"
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(index_path)
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "read-tree", base],
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0 or not index_path.is_file():
        raise BaselineError(f"git read-tree failed for immutable base: {completed.stderr}")
    return index_path


def git_common_dir(repo_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise BaselineError(f"git common-dir lookup failed: {completed.stderr}")
    value = Path(completed.stdout.strip())
    if not value.is_absolute():
        value = (repo_root / value).resolve()
    return value


def materialize_base_gitdir(
    *, output: Path, base: str, base_index: Path
) -> Path:
    """Build detached read-only Git metadata for the materialized base worktree."""
    git_dir = output / "base-git"
    (git_dir / "objects/info").mkdir(parents=True)
    (git_dir / "HEAD").write_text(f"{base}\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = true\n"
        "\tbare = false\n",
        encoding="utf-8",
    )
    (git_dir / "objects/info/alternates").write_text(
        "/git/objects\n", encoding="utf-8"
    )
    shutil.copy2(base_index, git_dir / "index")
    return git_dir


def _copy_baseline_binding(
    *, output: Path, evidence_root: Path, binding_output: Path
) -> None:
    """Publish the small immutable-base proof set into lane 01's namespace."""
    binding_output.mkdir(parents=True, exist_ok=True)
    if any(binding_output.iterdir()):
        raise BaselineError("baseline binding output must be empty")
    for name in (
        BASELINE_JUNIT_NAME,
        "baseline-identities.json",
        "baml_src-manifest.json",
        "baml_client-manifest.json",
        "base.index",
        "base.gitfile",
    ):
        src = output / name
        if not src.is_file():
            raise BaselineError(f"baseline binding source missing: {src}")
        shutil.copy2(src, binding_output / name)
    for source_name, target_name in (
        ("base-git", "base-git"),
        ("records", "nested-records"),
        ("artifacts", "nested-artifacts"),
    ):
        source = (
            output / source_name
            if source_name == "base-git"
            else evidence_root / source_name
        )
        if not source.is_dir():
            raise BaselineError(f"baseline nested evidence missing: {source}")
        shutil.copytree(source, binding_output / target_name)


def run_baseline_lane(
    *,
    repo_root: Path,
    base: str,
    output: Path,
    image: str,
    evidence_root: Path,
    binding_output: Path | None = None,
    record: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if image != IMMUTABLE_NEXTSEEK_IMAGE:
        raise BaselineError(f"mutable or unapproved image refused: {image}")
    output.mkdir(parents=True, exist_ok=True)
    extract_dir = output / "subject-tree"
    if extract_dir.exists():
        raise BaselineError("refuse overwrite of materialized subject tree")
    blob_manifest = materialize_base_tree(
        repo_root=repo_root, base=base, dest=extract_dir
    )
    for relative in ("schema_rag/duckdb", "schema_rag/embedding_models"):
        (extract_dir / relative).mkdir(parents=True, exist_ok=True)
    identities = baseline_identities(repo_root=repo_root, base=base)
    base_index = materialize_base_index(repo_root=repo_root, base=base, output=output)
    base_git_dir = materialize_base_gitdir(
        output=output, base=identities["subject_base"], base_index=base_index
    )
    common_git = git_common_dir(repo_root)
    base_gitfile = output / "base.gitfile"
    base_gitfile.write_text("gitdir: /baseline-git\n", encoding="utf-8")
    recorder = record or record_command
    ignores: list[str] = []
    for path in THREE_PYTEST_IGNORES:
        ignores.extend(["--ignore", path])
    pytest_writable = evidence_root / "artifacts" / "baseline-pytest"
    baml_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-v",
        f"{extract_dir}:/repo:ro",
        "-v",
        f"{extract_dir}/dmac_assistant/src/dmac_assistant/router:"
        "/repo/dmac_assistant/src/dmac_assistant/router",
        "-v",
        f"{extract_dir}/dmac_assistant/tools/e2e:"
        "/repo/dmac_assistant/tools/e2e",
        "-w",
        "/repo",
        image,
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
    ]
    pytest_argv = [
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
        "GIT_CONFIG_COUNT=1",
        "-e",
        "GIT_CONFIG_KEY_0=safe.directory",
        "-e",
        "GIT_CONFIG_VALUE_0=/repo",
        "-v",
        f"{pytest_writable}:/evidence",
        "-v",
        f"{extract_dir}:/repo:ro",
        "-v",
        f"{common_git}:/git:ro",
        "-v",
        f"{base_git_dir}:/baseline-git:ro",
        "-v",
        PINNED_PAIRED_ZIP_VOLUME,
        "-w",
        "/repo",
        image,
        "/app/.venv/bin/python",
        "-m",
        "pytest",
        "nextseek_api/cc_assistant/tests",
        *ignores,
        "--junitxml=/evidence/base-cc-assistant.junit.xml",
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    baml_record = recorder(
        evidence_root=evidence_root,
        name="01-baseline-baml",
        argv=baml_argv,
        writable_output=evidence_root / "artifacts" / "baseline-baml",
        repo_root=repo_root,
        declared_source_manifest=blob_manifest,
    )
    if baml_record.get("exit_code") != 0:
        raise BaselineError(
            f"immutable-base BAML command failed: exit {baml_record.get('exit_code')}"
        )
    (extract_dir / ".git").write_text(
        base_gitfile.read_text(encoding="utf-8"), encoding="utf-8"
    )
    pytest_record = recorder(
        evidence_root=evidence_root,
        name="01-baseline-pytest",
        argv=pytest_argv,
        writable_output=pytest_writable,
        repo_root=repo_root,
        declared_source_manifest=blob_manifest,
    )
    _publish_baseline_junit(pytest_writable=pytest_writable, output=output)
    baml_src = hashed_file_manifest(extract_dir / BAML_SRC_GLOB)
    baml_client = hashed_file_manifest(extract_dir / BAML_CLIENT_GLOB)
    (output / "baml_src-manifest.json").write_text(
        json.dumps(baml_src, indent=2) + "\n", encoding="utf-8"
    )
    (output / "baml_client-manifest.json").write_text(
        json.dumps(baml_client, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        **identities,
        "subject_blob_manifest": blob_manifest,
        "baml_src_manifest": baml_src,
        "baml_client_manifest": baml_client,
        "baml_record_exit": baml_record.get("exit_code"),
        "pytest_record_exit": pytest_record.get("exit_code"),
        "image": image,
    }
    (output / "baseline-identities.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if binding_output is not None:
        _copy_baseline_binding(
            output=output,
            evidence_root=evidence_root,
            binding_output=binding_output,
        )
    if pytest_record.get("exit_code") != 0:
        raise BaselineError(
            f"immutable-base pytest command failed: exit {pytest_record.get('exit_code')}"
        )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan 005 immutable-base materializer.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--base", default=PLAN005_BASE_COMMIT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence-root", type=Path, default=None)
    parser.add_argument("--binding-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    evidence_root = args.evidence_root or args.output / "recorder"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "artifacts").mkdir(exist_ok=True)
    (evidence_root / "records").mkdir(exist_ok=True)
    try:
        summary = run_baseline_lane(
            repo_root=args.repo_root,
            base=args.base,
            output=args.output,
            image=args.image,
            evidence_root=evidence_root,
            binding_output=args.binding_output,
        )
    except (BaselineError, RecordError) as exc:
        print(f"plan005_baseline failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: summary[k] for k in ("tool_head", "subject_base", "image")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
