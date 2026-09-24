"""The UserPromptSubmit hook names the newest staged turn on every turn (CC-RERUN-FINDINGS fix 3).

A resumed agent did not re-read /data/previous_turns/MANIFEST.md: r1-581 and r4-608 answered
about the wrong turn, and r6-1229 redid its own previous turn although that turn's full.json
was staged in turn-02/. The only "newest is turn N" pointer was in the memory file, which a
resumed conversation does not look at again. The hook runs before every turn, resumed or not,
so it now opens the injected context with the newest staged turn: which turn, what it asked,
what it returned, whether it carries sample UIDs, its files, and to read MANIFEST.md first.
It still resolves the vocabulary, and it still never blocks a turn.

Runs the real script with the host's sh and jq (the image installs jq; skipped where absent).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "entity_preamble.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")

NS_TURN = {
    "turn_id": 3, "folder": "turn-03", "user_query": "Find LUAD samples", "route": "nextseek_query",
    "mode": "graph_query", "count": 585, "total": 585, "truncated": False, "sample_uids": 585,
    "files": [{"file": "search_details.json", "holds": "..."}, {"file": "rows.json", "holds": "..."},
              {"file": "rows.csv", "holds": "..."}, {"file": "samples.csv", "holds": "..."}],
    "skipped": [],
}
CC_TURN = {
    "turn_id": 2, "folder": "turn-02", "user_query": "Which of those are current smokers?",
    "route": "container_cc", "mode": "cc",
    "files": [{"file": "answer.md", "holds": "..."}, {"file": "full.json", "holds": "..."}],
    "skipped": [],
}
COUNT_TURN = {
    "turn_id": 5, "folder": "turn-05", "user_query": "How many MSI-high samples?",
    "route": "nextseek_query", "mode": "graph_query", "count": 1, "total": 1, "truncated": False,
    "sample_uids": 0, "files": [{"file": "search_details.json", "holds": "..."}], "skipped": [],
}


def _manifest(tmp_path: Path, *turns: dict) -> Path:
    staged = tmp_path / "previous_turns"
    staged.mkdir()
    (staged / "manifest.json").write_text(json.dumps({
        "schema_version": "prior_turns/v1", "container_path": "/data/previous_turns",
        "turns": list(turns)}))
    return staged


def _entity_bin(tmp_path: Path, payload: str) -> Path:
    stub = tmp_path / "nextseek-entity-extract"
    stub.write_text(f"#!/bin/sh\nprintf '%s' '{payload}'\n")
    stub.chmod(0o755)
    return stub


def _run(tmp_path: Path, *, staged: Path | None = None, entity: Path | None = None,
         prompt: str = "Break those down by sex") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["NEXTSEEK_PREVIOUS_TURNS_DIR"] = str(staged or tmp_path / "absent")
    env["NEXTSEEK_ENTITY_EXTRACT_BIN"] = str(entity or tmp_path / "no-such-bin")
    return subprocess.run(["sh", str(HOOK)], input=json.dumps({"prompt": prompt}), env=env,
                          capture_output=True, text=True, timeout=30)


def _context(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    return out["hookSpecificOutput"]["additionalContext"]


def test_the_newest_ns_turn_is_named_with_its_files_and_uids(tmp_path):
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, NS_TURN, CC_TURN)))
    first = ctx.splitlines()[0]
    assert "turn 3" in first and "nextseek_query" in first
    assert '"Find LUAD samples"' in ctx
    assert "585 rows" in ctx and "Sample UIDs: 585" in ctx
    assert "/data/previous_turns/turn-03/" in ctx
    assert "rows.csv" in ctx and "samples.csv" in ctx
    assert "Read /data/previous_turns/MANIFEST.md first" in ctx
    assert "turn 2" not in first                            # only the newest is named


def test_the_newest_cc_turn_is_the_agents_own_with_its_files(tmp_path):
    """The r6-1229 shape: its own previous turn wrote full.json, staged in turn-02/."""
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, CC_TURN, NS_TURN)))
    assert "turn 2" in ctx.splitlines()[0] and "container_cc" in ctx
    assert "your own earlier turn" in ctx
    assert "/data/previous_turns/turn-02/" in ctx and "full.json" in ctx and "answer.md" in ctx


def test_a_count_only_turn_says_it_has_no_uids_and_to_keep_its_filter(tmp_path):
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, COUNT_TURN)))
    assert "no sample UIDs" in ctx
    assert "change only its RETURN" in ctx and "keep every MATCH and WHERE" in ctx


def test_both_notes_the_turn_first_then_the_vocabulary(tmp_path):
    vocab = '{"sampletypes":["MUS"],"assays":[],"keywords":[],"projects":[]}'
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, NS_TURN),
                        entity=_entity_bin(tmp_path, vocab)))
    assert ctx.index("turn 3") < ctx.index("NExtSEEK vocabulary auto-resolved")
    assert ctx.rstrip().endswith(vocab)


def test_the_vocabulary_alone_when_nothing_is_staged(tmp_path):
    vocab = '{"sampletypes":["NHP"],"assays":[],"keywords":[],"projects":[]}'
    ctx = _context(_run(tmp_path, entity=_entity_bin(tmp_path, vocab)))
    assert ctx.startswith("NExtSEEK vocabulary auto-resolved") and "previous_turns" not in ctx


def test_the_turn_note_survives_a_failed_vocabulary_lookup(tmp_path):
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, NS_TURN),
                        entity=_entity_bin(tmp_path, "not json")))
    assert "turn 3" in ctx and "vocabulary" not in ctx


@pytest.mark.parametrize("manifest", ["", "{not json", '{"turns": []}', '{"turns": "x"}'])
def test_nothing_to_say_emits_nothing_and_never_blocks(tmp_path, manifest):
    staged = tmp_path / "previous_turns"
    staged.mkdir()
    (staged / "manifest.json").write_text(manifest)
    proc = _run(tmp_path, staged=staged)
    assert proc.returncode == 0 and proc.stdout == ""


def test_a_turn_that_could_not_be_staged_lists_no_empty_file_list(tmp_path):
    odd = {"turn_id": 4, "folder": "turn-04", "user_query": "q", "route": "unknown", "mode": None,
           "files": [], "skipped": [{"file": "*", "reason": "OSError"}]}
    ctx = _context(_run(tmp_path, staged=_manifest(tmp_path, odd)))
    assert "turn 4" in ctx and "Files in" not in ctx
    assert "Read /data/previous_turns/MANIFEST.md first" in ctx
