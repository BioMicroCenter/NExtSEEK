"""The NS engine's run_root must reach the event stream.

Without it nothing can join a turn to its outputs/<ts>_<user>/ directory:
run_root lives only in the chat_nextseek session dict (orchestrator.py:339) and
QueryTask has no field for it. The collector's fallback is a timestamp window,
which is only unambiguous while runs are strictly sequential.

The mapping is task_id -> directory, NOT one directory per turn. The directory
belongs to the chat SESSION: log_dir is persisted into ChatSession.extra_state,
the next turn's adapter reloads it, and _ensure_query_log_dir
(orchestrator.py:329-331) then returns early rather than making a new one, so
every turn of a multi-turn case appends into turn 1's directory. Consumers must
de-duplicate on run_root instead of assuming uniqueness.

Nothing here can demonstrate that, and neither can the call-site tests in
test_within_chat_db.py: their fake run_query writes a fresh run_root_dir on every
call, which the real orchestrator does not do. Do not read either file as
evidence about directory lifetime -- it is a property of vendored chat_nextseek.

Vendored code is NOT touched. This reads the session dict the Django side already
holds, so startup/scripts/sync_chat_nextseek.sh cannot clobber it.
"""
from nextseek_api.services import cc_assistant as svc


def test_run_root_is_emitted_when_the_session_carries_one():
    events = []
    session = {"run_root_dir": "/app/outputs/260804_101500_demo"}
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), session)
    assert events == [("ns_run_root", {"run_root": "/app/outputs/260804_101500_demo"})]


def test_nothing_is_emitted_when_the_session_has_no_run_root():
    """A turn that never reached the orchestrator has no run_root. Emitting an
    empty one would make the collector look for a directory that never existed."""
    events = []
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), {})
    assert events == []


def test_a_broken_session_object_never_breaks_the_turn():
    """This is instrumentation. It must not be able to fail a real user's query."""
    class Hostile:
        def get(self, _k, _d=None):
            raise RuntimeError("boom")

    events = []
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), Hostile())
    assert events == []


def test_a_raising_send_event_never_breaks_the_turn():
    """The caller emits from a `finally`. An exception escaping this helper there
    would REPLACE the in-flight exception, so a broken event bus would destroy the
    real error instead of merely failing to record the join key."""
    def exploding_send_event(_e, _d):
        raise RuntimeError("event bus down")

    # The assertion IS reaching this line: _emit_ns_run_root must swallow the
    # RuntimeError and return None. Do not "fix" this test by deleting it as
    # assertion-less -- if the guard regresses, the call above propagates and
    # the test errors.
    assert svc._emit_ns_run_root(
        exploding_send_event, {"run_root_dir": "/app/outputs/x"}
    ) is None
