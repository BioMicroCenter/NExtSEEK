"""BAML client bootstrap for the evaluator CLI.

Auto-regenerates ``src/baml_client/`` on demand so a fresh clone of the repo
can run ``python -m chat_nextseek.evaluator`` without a manual
``baml-cli generate`` step.

Invariants:
- Called from ``__main__.py`` only; library imports never trigger it.
- Stale detection is an mtime compare against the newest ``.baml`` source.
- Hard fails with an actionable ``RuntimeError`` when tooling is missing;
  never silently falls back to a cached client.
"""
from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
from pathlib import Path

_DOC_ANCHOR = "src/chat_nextseek/evaluator/docs/operations.md#baml-regeneration"


def _resolve_baml_src() -> Path:
    try:
        anchor = importlib.resources.files("chat_nextseek.evaluator") / "baml_src"
        if isinstance(anchor, Path) and anchor.is_dir():
            return anchor
    except (ModuleNotFoundError, AttributeError):
        pass
    return Path(__file__).parent / "baml_src"


def _resolve_client_dir() -> Path:
    # src/chat_nextseek/evaluator/bootstrap.py -> src/baml_client
    return Path(__file__).resolve().parents[2] / "baml_client"


def _needs_regen(baml_src: Path, client_dir: Path) -> bool:
    inlined = client_dir / "inlinedbaml.py"
    if not inlined.exists():
        return True
    client_mtime = inlined.stat().st_mtime
    source_mtimes = [path.stat().st_mtime for path in baml_src.glob("*.baml")]
    if not source_mtimes:
        return False
    return max(source_mtimes) > client_mtime


def _bootstrap_baml_client() -> None:
    if os.environ.get("CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP") == "1":
        return

    baml_src = _resolve_baml_src()
    client_dir = _resolve_client_dir()

    if not _needs_regen(baml_src, client_dir):
        return

    cmd = ["uv", "run", "baml-cli", "generate", "--from", str(baml_src)]
    manual_command = f"uv run baml-cli generate --from {baml_src}"
    try:
        subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "BAML bootstrap could not locate `uv` on PATH. "
            f"Install uv (https://docs.astral.sh/uv/) and rerun, or execute manually: "
            f"`{manual_command}`. "
            f"See {_DOC_ANCHOR}."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", "replace")
            if isinstance(exc.stderr, (bytes, bytearray))
            else str(exc.stderr or "")
        )
        raise RuntimeError(
            f"baml-cli generate failed (exit {exc.returncode}). Stderr: {stderr}\n"
            f"Rerun manually: `{manual_command}`.\n"
            f"See {_DOC_ANCHOR}."
        ) from exc
