"""CLI for Plan 005 installed-plugin identity validation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from build_tools.plan005_validate_plugins.validate import (
    IMMUTABLE_VALIDATOR_IMAGE,
    PluginValidationError,
    validate_installed_plugins,
)

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
EXIT_OK = 0
EXIT_ERROR = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate installed Claude Code plugin identity manifests.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Repository root containing docker/cc-runtime plugin trees.",
    )
    parser.add_argument(
        "--validator-image",
        default=IMMUTABLE_VALIDATOR_IMAGE,
        help="Immutable locally verified Claude validator image digest.",
    )
    parser.add_argument(
        "--per-plugin-timeout",
        type=int,
        default=60,
        help="Per-plugin Docker validator timeout in seconds.",
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Run local identity checks only (primarily for unit tests).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print validation outcome JSON on success.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(
            f"plan005_validate_plugins failed: repo root not found: {repo_root}",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        outcome = validate_installed_plugins(
            repo_root=repo_root,
            validator_image=args.validator_image,
            per_plugin_timeout=args.per_plugin_timeout,
            skip_docker=args.skip_docker,
        )
    except PluginValidationError as exc:
        print(f"plan005_validate_plugins failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        sys.stdout.write(outcome.to_json())
    else:
        plugins = ", ".join(outcome.plugins)
        print(f"validated plugins: {plugins}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
