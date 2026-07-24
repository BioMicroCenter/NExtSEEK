from __future__ import annotations
import base64, json, time, urllib.request
from dataclasses import dataclass
from typing import Callable
from nessie_tests import route_observer as ro

BASE_PATH = "/nextseek_api/cc-assistant"


@dataclass
class DriveResult:
    session_id: str | None
    task_id: str | None
    payload: dict
    route_obs: ro.RouteObservation
    aborted_early: bool
    status: str


def basic_auth(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def make_default_clients(base_url: str, auth_header: str):
    def post_query(body: dict) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/query/async/", data=json.dumps(body).encode(),
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def get_progress(task_id: str) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/tasks/{task_id}/progress/",
            headers={"Authorization": auth_header})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    return post_query, get_progress


def drive(query: str, *, tier: str, post_query: Callable[[dict], dict],
          get_progress: Callable[[str], dict], session_id: str | None = None,
          mode: str = "standard", poll_interval_s: float = 2.0,
          route_timeout_s: float = 60.0, full_timeout_s: float = 200.0,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> DriveResult:
    body = {"query": query, "mode": mode}
    if session_id:
        body["session_id"] = session_id
    resp = post_query(body)
    task_id = resp.get("task_id")
    sess = resp.get("session_id") or session_id
    deadline = clock() + (route_timeout_s if tier == "route" else full_timeout_s)
    payload: dict = {"status": "pending", "progress": [], "result": None}
    aborted_early = False
    while True:
        payload = get_progress(task_id)
        if tier == "route" and ro.has_route_decided(payload):
            aborted_early = True
            break
        if payload.get("status") in ("completed", "error"):
            break
        if clock() >= deadline:
            break
        sleep(poll_interval_s)
    return DriveResult(sess, task_id, payload, ro.observe(payload),
                       aborted_early, payload.get("status", "pending"))
