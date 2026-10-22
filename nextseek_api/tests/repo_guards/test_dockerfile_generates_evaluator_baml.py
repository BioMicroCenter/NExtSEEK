"""The evaluator's BAML client is generated at image build, at the locked baml-py version."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_SRC = "NessieAI/chat_nextseek/src/chat_nextseek/evaluator/baml_src"


def test_dockerfile_generates_evaluator_client():
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert re.search(rf"baml-cli generate --from /app/{EVAL_SRC}\b", text)
    assert "/app/NessieAI/chat_nextseek/src/baml_client" in text  # the test -d guard


def test_evaluator_generator_version_matches_uv_lock():
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    locked = next(p["version"] for p in lock["package"] if p["name"] == "baml-py")
    gen = (REPO_ROOT / EVAL_SRC / "generators.baml").read_text()
    assert re.search(r'version "([^"]+)"', gen).group(1) == locked
