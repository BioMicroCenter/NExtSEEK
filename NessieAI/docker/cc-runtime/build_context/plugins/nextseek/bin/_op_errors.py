"""The op error codes the tools exit with (approach 1, piece 2). Stdlib only.

A copy of nextseek_api/assistant/op_errors.py EXIT and REASONS (this container has no NExtSEEK code);
NessieAI/tests/cc/test_op_road_parity.py pins the copies together. Today's exits stay; BUSY, TIME_UP and
PASS_NOT_ALLOWED are new.
"""
EXIT: dict[str, int] = {
    "VALIDATION": 3,
    "AGENT_FAILED": 4,
    "WRITE_BLOCKED": 5,
    "TRANSPORT_ERROR": 7,
    "AUTH_FAILED": 8,
    "STAGING_ERROR": 9,
    "BUSY": 10,
    "TIME_UP": 11,
    "PASS_NOT_ALLOWED": 12,
}
REASONS: tuple[str, ...] = ("model_unavailable", "deadline", "bad_output", "internal")
