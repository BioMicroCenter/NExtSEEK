"""CLI for deterministic generated-surface checks and writes."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from build_tools.gen_op_surfaces.constants import EXIT_ERROR, EXIT_NO_CHANGE
from build_tools.gen_op_surfaces.emit import check_surfaces, write_surfaces

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or check Plan 005 mechanical surfaces.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Render all targets to a temp directory and byte-compare committed files.",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Write generated targets into the repository.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_DEFAULT_REPO_ROOT,
        help="Repository root containing generated targets.",
    )
    parser.add_argument(
        "--tmpdir",
        type=Path,
        default=None,
        help="Optional parent directory for check-mode temp rendering.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = args.root.resolve()
    if not repo_root.is_dir():
        print(f"gen_op_surfaces failed: repo root not found: {repo_root}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.check:
            check_surfaces(repo_root=repo_root, tmp_dir=args.tmpdir)
            return EXIT_NO_CHANGE
        return write_surfaces(repo_root=repo_root)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return EXIT_ERROR
    except Exception:
        raise


if __name__ == "__main__":
    sys.exit(main())
