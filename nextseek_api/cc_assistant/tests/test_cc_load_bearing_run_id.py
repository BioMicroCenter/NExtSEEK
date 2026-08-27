"""Pin the load-bearing `str(run_id)` uses so the chat_log turn_id normalization
can never silently leak into them.

`str(run_id)` (the Celery task UUID) is load-bearing OUTSIDE the chat_log entry:
- artifact keys `f"{turn_id}/..."` and the on-disk `output/artifacts/<turn_id>/`
  layout, fed by `_publish_artifacts(turn_id=str(run_id))`;
- `CCSessionTranscript.turn_id` (a CharField in a unique_together), fed by
  `TurnCompletePayload(turn_id=str(run_id))` and persisted verbatim.
Only the chat_log ENTRY's turn_id changed to a sequential int; these must not.
"""
from pathlib import Path

_ENGINE = (Path(__file__).resolve().parents[1] / "cc_engine.py").read_text()
_SERVICE = (
    Path(__file__).resolve().parents[2] / "services" / "cc_assistant.py"
).read_text()


def test_publish_artifacts_still_keyed_by_str_run_id():
    # The post-turn publish keys artifacts by the run UUID, not the chat id.
    assert "_publish_artifacts(" in _ENGINE
    assert "turn_id=str(run_id)" in _ENGINE


def test_turn_complete_payload_still_carries_str_run_id():
    # The payload's turn_id (transcript/artifact identity) stays the run UUID.
    assert "turn_id=str(run_id)" in _ENGINE


def test_cc_transcript_persisted_with_payload_turn_id():
    # CCSessionTranscript.turn_id (unique_together) is fed from payload.turn_id,
    # i.e. still the run UUID — NOT the chat_log entry's sequential int.
    assert "turn_id=payload.turn_id" in _SERVICE
