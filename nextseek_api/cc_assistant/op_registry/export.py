"""Canonical deterministic ops.json export and checks (Plan 005 Task 3)."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.models import OpList, OpSpec
from nextseek_api.cc_assistant.op_registry.ops import OPS

CANONICAL_OPS_PATH = Path(__file__).resolve().parent / "ops.json"
BAKED_OPS_RELATIVE = Path("context") / "ops.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLUGINS_ROOT = _REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
DEFAULT_DOCKERFILE = _REPO_ROOT / "docker" / "cc-runtime" / "Dockerfile"


def canonical_ops_bytes(ops: list[OpSpec]) -> bytes:
    """Render strict OpList dumps as canonical sorted JSON bytes."""
    payload = OpList.dump_python(ops, mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def export_target_paths(
    *,
    plugins_root: Path,
    dockerfile_path: Path,
    canonical_path: Path = CANONICAL_OPS_PATH,
) -> tuple[Path, ...]:
    """Return canonical ops.json plus every installed plugin baked context path."""
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    baked = tuple(
        plugins_root / plugin / BAKED_OPS_RELATIVE for plugin in discovery.plugins
    )
    return (canonical_path, *baked)


def _compare_targets(expected: bytes, targets: tuple[Path, ...]) -> None:
    for path in targets:
        if not path.is_file():
            raise SystemExit(f"export check failed: missing {path}")
        actual = path.read_bytes()
        if actual != expected:
            raise SystemExit(f"export check failed: stale bytes at {path}")


def check_export(
    *,
    canonical_path: Path = CANONICAL_OPS_PATH,
    plugins_root: Path = DEFAULT_PLUGINS_ROOT,
    dockerfile_path: Path = DEFAULT_DOCKERFILE,
    ops: list[OpSpec] | None = None,
) -> None:
    """Regenerate to a temp directory and byte-compare; never writes the repo."""
    source_ops = OPS if ops is None else ops
    expected = canonical_ops_bytes(source_ops)
    targets = export_target_paths(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
        canonical_path=canonical_path,
    )
    with tempfile.TemporaryDirectory(prefix="op-registry-export-check-") as tmp:
        tmp_root = Path(tmp)
        for target in targets:
            if target == canonical_path:
                temp_path = tmp_root / "canonical" / "ops.json"
            else:
                temp_path = tmp_root / "baked" / target.relative_to(plugins_root)
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(expected)
            if temp_path.read_bytes() != expected:
                raise SystemExit(
                    f"export check failed: temp render mismatch at {temp_path}"
                )
    _compare_targets(expected, targets)


def write_export(
    *,
    canonical_path: Path = CANONICAL_OPS_PATH,
    plugins_root: Path = DEFAULT_PLUGINS_ROOT,
    dockerfile_path: Path = DEFAULT_DOCKERFILE,
    ops: list[OpSpec] | None = None,
) -> bytes:
    """Write canonical ops.json and every installed plugin baked copy."""
    source_ops = OPS if ops is None else ops
    payload = canonical_ops_bytes(source_ops)
    targets = export_target_paths(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
        canonical_path=canonical_path,
    )
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export canonical ops.json surfaces.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Regenerate to a temp dir and byte-compare committed targets.",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Write canonical and baked ops.json targets.",
    )
    parser.add_argument(
        "--canonical-path",
        type=Path,
        default=CANONICAL_OPS_PATH,
        help="Override canonical ops.json path (primarily for tests).",
    )
    parser.add_argument(
        "--plugins-root",
        type=Path,
        default=DEFAULT_PLUGINS_ROOT,
        help="Plugin source tree scanned by the install oracle.",
    )
    parser.add_argument(
        "--dockerfile-path",
        type=Path,
        default=DEFAULT_DOCKERFILE,
        help="Dockerfile parsed for installed plugin COPY/PATH targets.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.check:
        check_export(
            canonical_path=args.canonical_path,
            plugins_root=args.plugins_root,
            dockerfile_path=args.dockerfile_path,
        )
        return 0
    write_export(
        canonical_path=args.canonical_path,
        plugins_root=args.plugins_root,
        dockerfile_path=args.dockerfile_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
