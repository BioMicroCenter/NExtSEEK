"""The NS engine's run_root must reach the event stream.

Without it nothing can join a turn to its outputs/<ts>_<user>/ directory:
run_root lives only in the chat_nextseek session dict (orchestrator.py:335) and
QueryTask has no field for it. The collector's fallback is a timestamp window,
which is only unambiguous while runs are strictly sequential.

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
