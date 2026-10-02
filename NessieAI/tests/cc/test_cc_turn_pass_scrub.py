"""Spec piece 1, scrubbing. Once the password leaves the agent's env, the scrub has to take it from the login
Django holds for the turn; and a scrub that was given no password must never watermark a transcript as clean, or
a transcript written before this change, still holding the password, would be marked clean and handed to the
summarizer. Hermetic: tmp_path only."""
from __future__ import annotations

import ast
import base64
from pathlib import Path

import NessieAI.cc.turn as cc_turn
from NessieAI.cc import cc_engine

PW = "hunter2-s3cr3t"
USER = "demo"
PASS = "T" * 43
BASIC = base64.b64encode(f"{USER}:{PW}".encode()).decode()
# What a transcript written before the turn pass can hold: the plaintext and the Basic pair.
DIRTY = (
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"NEXTSEEK_PASSWORD={PW}\\n"'
    "}]}}\n"
    f'{{"type":"user","message":{{"content":"> Authorization: Basic {BASIC}"}}}}\n'
).encode()
# The agent's env after the change: a name and a pass, no password.
CONTAINER_ENV = {"NEXTSEEK_USERNAME": USER, "API_USER": USER, "NEXTSEEK_TURN_PASS": PASS}


def _store(cc_state: Path, body: bytes = DIRTY) -> Path:
    folder = cc_state / "projects" / "-home-user"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "sess.jsonl"
    path.write_bytes(body)
    return path


def _verified(path: Path) -> bool:
    return cc_engine.transcript_is_verified_scrubbed(path, path.read_bytes())


def test_a_pre_pass_transcript_holding_the_password_is_scrubbed_not_watermarked_clean(tmp_path):
    state = tmp_path / "cc-state" / "sess-a"
    path = _store(state)
    # The mistake this change prevents: a scrub fed the container env, which no longer holds the password.
    cc_engine.scrub_transcript_store(state, CONTAINER_ENV)
    assert PW.encode() in path.read_bytes()
    assert not _verified(path), "a scrub with no password must never mark a file clean"
    # What run_cc_turn does now: the login Django holds, plus the pass.
    cc_engine.scrub_transcript_store(state, cc_engine.scrub_secrets(api_user=USER, api_pass=PW, turn_pass=PASS))
    raw = path.read_bytes()
    assert PW.encode() not in raw and BASIC.encode() not in raw
    assert _verified(path)


def test_the_pass_is_scrubbed_but_a_password_blind_scrub_writes_no_watermark(tmp_path):
    state = tmp_path / "cc-state" / "sess-b"
    path = _store(state, f'{{"type":"user","message":{{"content":"NEXTSEEK_TURN_PASS={PASS}"}}}}\n'.encode())
    report = cc_engine.scrub_transcript_store(
        state, cc_engine.scrub_secrets(api_user=USER, api_pass=None, turn_pass=PASS))
    assert report.rewritten == 1
    assert PASS.encode() not in path.read_bytes()
    assert not _verified(path)


def test_scrub_secrets_uses_the_names_the_variant_builder_reads():
    assert cc_engine.scrub_secrets(api_user=USER, api_pass=PW, turn_pass=PASS) == {
        "NEXTSEEK_USERNAME": USER, "NEXTSEEK_PASSWORD": PW, "NEXTSEEK_TURN_PASS": PASS}
    assert cc_engine.scrub_secrets(api_user=None, api_pass=None) == {}
    assert "NEXTSEEK_TURN_PASS" in cc_engine._REDACTED_ENV_KEYS
    assert cc_engine._redact_env({"NEXTSEEK_TURN_PASS": PASS})["NEXTSEEK_TURN_PASS"] == "<REDACTED>"


_SCRUBBERS = {"scrub_transcript_store", "scrub_sibling_transcript_stores", "_read_turn_transcript",
              "_scrub_secret_bytes", "transcript_scrubber"}


def _function(module, name):
    tree = ast.parse(Path(module.__file__).with_suffix(".py").read_text())
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def _scrub_calls(function):
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            if (func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)) in _SCRUBBERS:
                yield node


def _names(call):
    values = list(call.args) + [keyword.value for keyword in call.keywords]
    return {node.id for value in values for node in ast.walk(value) if isinstance(node, ast.Name)}


def test_run_cc_turn_never_scrubs_with_the_container_env():
    calls = list(_scrub_calls(_function(cc_engine, "run_cc_turn")))
    assert len(calls) >= 4, "the two transcript captures and the two store scrubs"
    for call in calls:
        assert "environment" not in _names(call), f"line {call.lineno} scrubs with the container env"
        assert "scrub_env" in _names(call), f"line {call.lineno} does not scrub with Django's held login"


def test_the_staging_scrubber_is_built_from_the_held_login():
    (call,) = [c for c in _scrub_calls(_function(cc_turn, "start_task"))
               if getattr(c.func, "attr", None) == "transcript_scrubber"]
    (argument,) = call.args
    assert isinstance(argument, ast.Call) and getattr(argument.func, "attr", None) == "scrub_secrets"


MAPPINGS = '{"scratch":"/mnt/scratch/alice"}'


def test_ruling_r5_a_transcript_keeps_the_path_mappings_and_is_watermarked(tmp_path):
    """Operator ruling R5: DMAC_PATH_MAPPINGS is user-facing paths, not a credential; transcripts keep it."""
    state = tmp_path / "cc-state" / "sess-c"
    escaped = MAPPINGS.replace('"', '\\"')
    body = (f'{{"type":"user","message":{{"content":"DMAC_PATH_MAPPINGS={MAPPINGS}"}}}}\n'
            f'{{"type":"user","message":{{"content":"{escaped}"}}}}\n').encode()
    path = _store(state, body)
    secrets = cc_engine.scrub_secrets(api_user=USER, api_pass=PW, turn_pass=PASS)
    secrets["DMAC_PATH_MAPPINGS"] = MAPPINGS
    cc_engine.scrub_transcript_store(state, secrets)
    raw = path.read_bytes()
    assert MAPPINGS.encode() in raw and escaped.encode() in raw
    assert b"<REDACTED>" not in raw
    assert _verified(path)


def test_ruling_r5_log_lines_still_mask_the_path_mappings():
    red = cc_engine._redact_env({"DMAC_PATH_MAPPINGS": MAPPINGS, "NEXTSEEK_PASSWORD": PW})
    assert red["DMAC_PATH_MAPPINGS"] == "<REDACTED>" and red["NEXTSEEK_PASSWORD"] == "<REDACTED>"

