"""NS-route in-image runner for the DMAC Assistant LLM router.

Invoked by the bridge as:
    python /opt/dmac/runner_ns.py [--session <id>]

Responsibilities (in execution order):
  1. fd-shuffle (module level): save fd 1 to _EVENTS_FD, then dup2(2, 1) so
     print()/sys.stdout.write() lands on docker exec's stderr channel. JSONL
     events are written exclusively to _EVENTS_FD via os.write().
  2. Test opt-out via DMAC_RUNNER_NS_NO_REMAP env var.
  3. Read user query from stdin (one line).
  4. Build AssistantClient from env (NEXTSEEK_URL, API_USER, API_PASS).
  5. Call client.run_query, translate terminal/events to the JSONL contract
     the bridge ns_adapter expects (recon:nsRoute section 3).
  6. On uncaught exception, emit ns_runner_error and exit non-zero.

See docs/superpowers/specs/2026-06-08-nextseek-shared-cred-sidecar-design.md.
"""

# THIS BLOCK MUST BE THE FIRST EXECUTABLE STATEMENT IN THE MODULE, AT MODULE
# LEVEL (NOT INSIDE __main__). DO NOT INSERT ANY IMPORT THAT BINDS OR WRAPS
# sys.stdout / sys.stderr ABOVE THIS BLOCK. Plain `import os` is fine -- it
# does not touch sys.stdout. The risk is any line that calls
# `sys.stdout = ...` or wraps the stream in a Tee-like object.
import os as _os


def _perform_event_channel_remap() -> int:
    """Save original fd 1 then redirect fd 1 -> fd 2. Returns the saved fd."""
    saved = _os.dup(1)
    _os.dup2(2, 1)
    return saved


if _os.environ.get("DMAC_RUNNER_NS_NO_REMAP"):
    _EVENTS_FD = 1  # test opt-out: leave fd table untouched
else:  # pragma: no cover  -- production-only branch; tests always set DMAC_RUNNER_NS_NO_REMAP=1
    _EVENTS_FD = _perform_event_channel_remap()
# After this point in production: fd 1 -> docker stderr pipe;
# _EVENTS_FD -> docker stdout pipe. In test mode (env var set):
# both _EVENTS_FD and fd 1 still point at the test's stdout; the
# test exercises _perform_event_channel_remap() under a controlled
# fd capture instead.

# All other imports go below -- they may touch sys.stdout but only after the
# remap is complete.
import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from typing import Any  # noqa: E402

# httpx is imported here at module level (stage-2 import block) so the except
# clauses in main() that reference httpx.HTTPStatusError / httpx.TransportError
# always resolve the name from module scope. If httpx import were deferred into
# the try block inside main(), a failure of that import would leave httpx unbound
# as a function-local name; the first except clause would then raise
# UnboundLocalError, escaping the BaseException catch-all and producing a
# traceback with no ns_runner_error JSONL. httpx emits nothing to stdout on
# import, so moving it here is safe relative to the fd-remap invariant above.
import httpx  # noqa: E402

# Path resolution: in the image, runner_ns.py is at /opt/dmac/runner_ns.py and
# _assistant_client.py/_assistant_models.py are COPYd to /opt/dmac/ (T11).
# For host unit tests, those modules live in build_context/plugins/nextseek/bin/.
# We try the script's own directory first, then fall back to the repo bin dir
# relative to this file's location.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_BIN_DIR = os.path.join(
    os.path.dirname(_SCRIPT_DIR), "build_context", "plugins", "nextseek", "bin"
)
# Insert in reverse-priority order so that after both inserts, _SCRIPT_DIR ends
# up at a lower index (higher precedence) than _REPO_BIN_DIR.
for _p in (_REPO_BIN_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# Failure-signal constants -- preserved from the original runner, aligned with
# recon:nsRoute section 1b and ns_adapter._has_failure_signal logic.
_FAILURE_STATUSES = frozenset({"error", "partial", "failure"})

# The exact error string the AssistantClient emits when the polling deadline
# passes or the task ends with no terminal event and no result (see
# _assistant_client.STREAM_ENDED_SENTINEL). Used to distinguish a synthetic-
# terminal sentinel from a genuine query_error. Drift-pinned in test_runner_ns.py.
_STREAM_ENDED_WITHOUT_TERMINAL = "stream ended without terminal event"


def _has_failure_signal(payload: dict[str, Any]) -> bool:
    """True if ANY of the 5 union'd failure signals is present per recon:nsRoute section 1b."""
    if payload.get("status") in _FAILURE_STATUSES:
        return True
    if payload.get("error_type"):
        return True
    if payload.get("error"):  # top-level legacy
        return True
    debug = payload.get("debug") or {}
    if debug.get("error"):
        return True
    if debug.get("fatal_error"):
        return True
    return False


def _emit_jsonl(event_name: str, payload: dict[str, Any]) -> None:
    """Write one JSONL event line to the saved fd via os.write (NEVER print)."""
    line = json.dumps({"event": event_name, "payload": payload}, ensure_ascii=False)
    os.write(_EVENTS_FD, line.encode("utf-8") + b"\n")


def _build_assistant_client() -> Any:
    """Construct AssistantClient from environment variables.

    Reads NEXTSEEK_URL, NEXTSEEK_ASSISTANT_PREFIX (default nextseek_api/assistant),
    API_USER, API_PASS -- mirrors the pattern used by T8's _run_viewset in
    build_context/plugins/nextseek/bin/_nextseek_runner.py.
    """
    import _assistant_client as ac  # noqa: PLC0415 -- deferred for path resolution

    return ac.AssistantClient(
        base_url=os.environ["NEXTSEEK_URL"],
        assistant_prefix=os.environ.get(
            "NEXTSEEK_ASSISTANT_PREFIX", "nextseek_api/assistant"
        ),
        auth=(os.environ.get("API_USER", ""), os.environ.get("API_PASS", "")),
    )


def _typed_failure_events(
    error_type: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Return the ordered (event_name, payload) pairs for a typed failure.

    Used by the AUTH_FAILED, non-401 HTTPStatusError, and TRANSPORT_ERROR
    exception branches, which all share the same two-event shape:
      1. ns_runner_error_type companion (carries error_type for bridge debug log)
      2. failure-shaped query_complete (carries reply, status, error_type)

    Debug propagation is intentionally not wired here because the HTTP-error
    branches have no terminal dict to propagate debug from; the terminal-
    translation path uses _translate_terminal instead.
    """
    return [
        (
            "ns_runner_error_type",
            {"agent": None, "error_type": error_type},
        ),
        (
            "query_complete",
            {
                "reply": "<redacted; see ns_query_complete_with_error frame>",
                "status": "error",
                "error_type": error_type,
            },
        ),
    ]


def _translate_terminal(terminal: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Translate a terminal dict from AssistantClient into ordered (event_name, payload) pairs.

    Covers four cases:
      1. Genuine __error__ (non-sentinel) -> query_error + ns_runner_error_type companion
      2. Stream-ended sentinel (__error__ == _STREAM_ENDED_WITHOUT_TERMINAL, agent is None)
         -> synthetic failure-shaped query_complete (no companion)
      3. Failure-signal terminal (no __error__, but _has_failure_signal) ->
         ns_runner_error_type companion + failure-shaped query_complete
      4. Clean success -> query_complete with reply/bundle_id/session_id/debug

    Callers must emit the returned list in order via _emit_jsonl.
    """
    if "__error__" in terminal:
        # Distinguish the stream-ended-without-terminal sentinel from a
        # genuine query_error. The client represents the sentinel as
        # {"__error__": _assistant_client.STREAM_ENDED_SENTINEL, "agent": None}
        # (emitted when polling deadline passes or status becomes terminal with
        # no terminal event in progress). A genuine query_error carries a
        # non-None agent or a different error string.
        sentinel_error = terminal.get("__error__", "")
        agent = terminal.get("agent")
        is_synthetic_sentinel = (
            sentinel_error == _STREAM_ENDED_WITHOUT_TERMINAL and agent is None
        )

        if is_synthetic_sentinel:
            # Match the old runner's synthetic-terminal shape so the bridge's
            # _detect_query_complete_failure fires and routes to
            # ns_query_complete_with_error (not bare ns_exec_truncated).
            return [
                (
                    "query_complete",
                    {
                        "reply": (
                            "<runner synthetic terminal: assistant viewset "
                            "stream ended without query_complete or query_error>"
                        ),
                        "status": "error",
                        "error_type": "RunnerSyntheticTerminal",
                    },
                )
            ]
        else:
            # Genuine query_error from the viewset. Redact the error text;
            # emit ns_runner_error_type companion for the bridge's debug log.
            return [
                (
                    "query_error",
                    {"error": "<redacted>", "agent": agent},
                ),
                (
                    "ns_runner_error_type",
                    {"agent": agent, "error_type": "RedactedByRunner"},
                ),
            ]

    elif _has_failure_signal(terminal):
        # query_complete with a failure signal -- redact the reply so the
        # bridge's _detect_query_complete_failure routes to
        # ns_query_complete_with_error (vet findings 11/13/19).
        #
        # The plan deliberately propagates `debug` unredacted here (vet
        # finding 19: a soft-failure signal the detection misses must still
        # reach the bridge unchanged; error text now originates from the
        # NExtSEEK viewset, not a creds-holding in-container library), while
        # query_error.error stays the redacted literal above.
        return [
            (
                "ns_runner_error_type",
                {
                    "agent": None,
                    "error_type": terminal.get("error_type") or "UnknownFailure",
                },
            ),
            (
                "query_complete",
                {
                    "reply": "<redacted; see ns_query_complete_with_error frame>",
                    "status": "error",
                    "debug": terminal.get("debug"),
                },
            ),
        ]

    else:
        # Clean success -- propagate reply, bundle_id, session_id, debug.
        #
        # The plan deliberately propagates `debug` unredacted (vet finding 19:
        # a soft-failure signal the detection misses must still reach the bridge
        # unchanged; error text now originates from the NExtSEEK viewset, not a
        # creds-holding in-container library), while query_error.error stays the
        # redacted literal.
        return [
            (
                "query_complete",
                {
                    "reply": terminal["reply"],
                    "bundle_id": terminal.get("bundle_id"),
                    "session_id": terminal.get("session_id"),
                    "debug": terminal.get("debug"),
                },
            )
        ]


def main(argv: Sequence[str] = ()) -> int:
    """Entry point: read query from stdin, drive run_query, emit JSONL events."""
    parser = argparse.ArgumentParser(prog="runner_ns")
    parser.add_argument("--session", default=None, help="session id (passed to AssistantClient)")
    args = parser.parse_args(argv)

    # Read the single-line query from stdin (spec line 90).
    # Blocks until the bridge writes the query line; the bridge's turn deadline is the backstop.
    user_text = sys.stdin.readline().rstrip("\n")
    if not user_text:
        _emit_jsonl("ns_runner_error", {"error_type": "EmptyQuery"})
        sys.exit(2)

    def _forward_progress(name: str, data: dict[str, Any]) -> None:
        """on_event callback: forward only the names the bridge recognises."""
        if name in ("agent_started", "agent_complete"):
            _emit_jsonl(name, dict(data))

    try:
        client = _build_assistant_client()
        terminal, _ = client.run_query(
            user_text,
            mode="standard",
            session_id=args.session,
            on_event=_forward_progress,
        )

        # -- Translate the terminal dict --
        for event_name, payload in _translate_terminal(terminal):
            _emit_jsonl(event_name, payload)

        return 0

    except httpx.HTTPStatusError as exc:
        # 401 from the viewset -> AUTH_FAILED typed failure. Never echo creds.
        if exc.response.status_code == 401:
            for event_name, payload in _typed_failure_events("AUTH_FAILED"):
                _emit_jsonl(event_name, payload)
        else:
            for event_name, payload in _typed_failure_events("HTTPStatusError"):
                _emit_jsonl(event_name, payload)
        sys.exit(1)

    except httpx.TransportError:
        # Viewset unreachable (DNS failure, connection refused, etc.)
        for event_name, payload in _typed_failure_events("TRANSPORT_ERROR"):
            _emit_jsonl(event_name, payload)
        sys.exit(1)

    except BaseException as exc:  # noqa: BLE001 -- R-03 demands BaseException catch
        # R-03: NEVER str(exc), NEVER repr(exc), NEVER exc.args.
        _emit_jsonl("ns_runner_error", {"error_type": type(exc).__name__})
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover  -- entry-point guard; tests call main() directly
    sys.exit(main(sys.argv[1:]))
