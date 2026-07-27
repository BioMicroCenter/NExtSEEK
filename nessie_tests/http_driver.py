from __future__ import annotations
import base64, json, time, urllib.request
from dataclasses import dataclass
from typing import Callable
from nessie_tests import route_observer as ro

BASE_PATH = "/nextseek_api/cc-assistant"


# A saturated web tier (e.g. several consecutive container_cc turns) can stall a
# request for minutes. 30s here used to surface as a TimeoutError that the runner
# recorded as a case `error`, discarding the route observation with it — an
# infrastructure hiccup masquerading as a product failure.
SOCKET_TIMEOUT_S = 120

# Consecutive poll failures tolerated before we call the endpoint down and let
# the error propagate (the runner records that as a case `error`, which is the
# honest label for infrastructure). Interspersed blips reset the count, so a
# slow-but-alive server keeps polling to its deadline instead.
MAX_CONSECUTIVE_POLL_ERRORS = 5


@dataclass
class DriveResult:
    session_id: str | None
    task_id: str | None
    payload: dict
    route_obs: ro.RouteObservation
    aborted_early: bool
    status: str
    poll_errors: int = 0


def basic_auth(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def make_default_clients(base_url: str, auth_header: str, timeout_s: float = SOCKET_TIMEOUT_S):
    def post_query(body: dict) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/query/async/", data=json.dumps(body).encode(),
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode())

    def get_progress(task_id: str) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/tasks/{task_id}/progress/",
            headers={"Authorization": auth_header})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode())

    return post_query, get_progress


def drive(query: str, *, tier: str, post_query: Callable[[dict], dict],
          get_progress: Callable[[str], dict], session_id: str | None = None,
          force_new: bool = False,
          mode: str = "standard", poll_interval_s: float = 2.0,
          route_timeout_s: float = 60.0, full_timeout_s: float = 600.0,
          max_consecutive_poll_errors: int = MAX_CONSECUTIVE_POLL_ERRORS,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> DriveResult:
    """Drive one turn to completion (full tier) or to route_decided (route tier).

    ``force_new`` asks the server for a fresh ChatSession. Without it the API
    falls back to the caller's most recently updated session, which silently
    joins every case into one conversation and leaks results_history, pinned
    bundles and pipeline state across cases. It is ignored once ``session_id``
    is known, so a case's later turns stay in the session its seed opened.
    """
    body = {"query": query, "mode": mode}
    if session_id:
        body["session_id"] = session_id
    elif force_new:
        body["force_new"] = True
    resp = post_query(body)
    task_id = resp.get("task_id")
    sess = resp.get("session_id") or session_id
    deadline = clock() + (route_timeout_s if tier == "route" else full_timeout_s)
    payload: dict = {"status": "pending", "progress": [], "result": None}
    aborted_early = False
    poll_errors = 0
    consecutive_errors = 0
    while True:
        try:
            payload = get_progress(task_id)
        except Exception:  # transient socket/proxy hiccup — keep the case alive
            poll_errors += 1
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_poll_errors:
                raise  # endpoint is down, not slow — report it as infra
        else:
            consecutive_errors = 0
            if tier == "route" and ro.has_route_decided(payload):
                aborted_early = True
                break
            if payload.get("status") in ("completed", "error"):
                break
        if clock() >= deadline:
            break
        sleep(poll_interval_s)
    return DriveResult(sess, task_id, payload, ro.observe(payload),
                       aborted_early, payload.get("status", "pending"), poll_errors)
