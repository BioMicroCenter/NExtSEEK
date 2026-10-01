"""The Nessie lane: proves a build did not break Nessie.

Stage 1 checks that every Nessie route and chat-page control exists, with no model
call. Stages 2 and 3 type three NS questions and one CC question into the real
chat page, then check the results through the API, including whether the sessions
endpoints report what the page showed.

THIS IS THE FILE TO EXTEND when Nessie changes. A new question is a row in
QUESTIONS. A new endpoint is a registry entry in ci/routes.py plus a check here.
Spec: docs/superpowers/specs/2026-09-11-nessie-ci-lane-design.md.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci import routes
from ci.smoke.client import GuardedSession
from ci.smoke.conftest import _cred, _guard_context, login_storage_state, web_session

pytestmark = [pytest.mark.nessie, pytest.mark.flow, pytest.mark.profiles("local", "dev")]


# --------------------------------------------------------------------------- #
# the questions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Question:
    key: str
    text: str
    route: str          # the engine the router must pick: nextseek_query | container_cc
    path: str           # system | api | graph | cc
    bundle: bool        # the turn registers an NS bundle (JSON and Metadata downloads)
    cc_artifact: bool   # the turn leaves a Container-CC artifact


# NS first, CC last: a completed CC turn makes the chat sticky, so an NS question
# asked after it would be answered by CC.
QUESTIONS: tuple[Question, ...] = (
    Question("capabilities", "What can you do?", "nextseek_query", "system", False, False),
    # Changed from "api" to "graph" knowingly on 2026-09-21, and the old value is the
    # point of the note. This question is a sample type plus a treatment attribute, which
    # is metadata, and F1 (promoting the measured prompt set to the default) moved exactly
    # that shape from advanced_search to graph_query: the descriptive-attribute rule now
    # reads "-> graph_query", and six metadata triggers were added to the graph while the
    # three sample-search ones left api_preferred. So the graph path IS the correct answer
    # here and the old pin was asserting pre-F1 behaviour.
    #
    # COVERAGE NOTE: with this and impact_studies both on the graph, the lane no longer
    # exercises the NS **api** path at all. That is a real gap, not a tidy-up. What is
    # still api_preferred after F1 is catalog records, the full record or an export of a
    # named UID, PATCH on a UID, and any non-metadata intent -- so an api-path question
    # for this lane would have to be one of those (e.g. the full metadata record for a
    # known UID), which is a new paid turn and the operator's call to add.
    Question("ndma_mice", "What mice are treated with NDMA?", "nextseek_query", "graph", True, False),
    Question("impact_studies", "What studies are in IMPACT?", "nextseek_query", "graph", True, False),
    Question("nhp_graph", "Make me a histogram image of NHP species", "container_cc", "cc", False, True),
)

MAX_CHAT_POSTS = len(QUESTIONS)
SPEND_CEILING_USD = 1.00
# The CC turn may report up to this much for each minute it ran, never less than one
# minute's worth. A flat $0.50 failed real two-minute turns ($0.51 in run 5, $0.54 in
# run 6) that sat well inside the engine's own budget (NEXTSEEK_CC_MAX_BUDGET_USD).
CC_COST_PER_MINUTE_USD = 0.50
TURN_TIMEOUT_S = {"nextseek_query": 300, "container_cc": 240}
LANE_DEADLINE_S = 720
POLL_INTERVAL_S = 2.0
# How long one progress poll may wait for its answer. The 2026-09-11 run 3 lost the
# CC turn to a single poll that hit the old 60 s while the app was starved of memory.
POLL_READ_TIMEOUT_S = 180
CHAT_PATH = "/nextseek_api/cc-assistant/query/async/"
GRAPH_MODE = "graph_query"       # the mode the NS graph branch records on its bundle
# The modes the REST branch records on a search bundle; search_results answers only
# these (download_artifact in nextseek_api/services/assistant.py).
API_MODES = ("new_search", "refine_last_search")
ROUTE_ENTRY_AGENT = "router"     # the Debug panel's agent label for a route_decided entry
NESSIE_PREFIXES = ("assistant/", "cc-assistant/", "nessie/", "evaluator/", "schema_rag/")
# A passing lane keeps its chat too (finish_chat), so an intermittent failure has a
# passing chat to be diffed against. It titles the chat with this marker plus the UTC
# time, and deletes the write account's older marked chats beyond this many, the one
# it just kept included. A failing lane's chat never carries the marker, so it is
# never pruned.
PASSING_CHAT_TITLE = "CI Nessie lane passed"
KEEP_PASSING_CHATS = 3

# The retrieve body (RetrieveRequest in nextseek_api/models.py). Its only required
# field is `query`, but a body naming neither session_id nor schema_url always
# answers SESSION_MISSING_OR_EXPIRED with no endpoints, so the check adds this
# instance's own schema URL. The server recognises that URL and builds the document
# in-process rather than fetching it (is_self_schema_url in
# NessieAI/schema_rag/schema_processor.py), and ingests it on first use.
SCHEMA_RAG_QUERY = {"query": "list the samples in a project"}
SELF_SCHEMA_PATH = "/nextseek_api/schema/"


# --------------------------------------------------------------------------- #
# pure helpers (pinned by test_nessie_unit.py)
# --------------------------------------------------------------------------- #

def classify_request(method: str, url: str) -> str:
    """'lane' for the chat POST this lane sends, 'blocked' for any other paid POST,
    'pass' for everything else. Read from the registry, so a new paid route is
    blocked the day it is declared."""
    if (method or "").upper() != "POST":
        return "pass"
    route = routes.match(url)
    if route is None:
        return "pass"
    if route.lane == "nessie":
        return "lane"
    if route.exclude == "EXCLUDE_COST":
        return "blocked"
    return "pass"


class ChatBudget:
    """At most MAX_CHAT_POSTS chat POSTs, and the reported spend against the ceiling.

    The ceiling counts what it always has: Claude Code's own total_cost_usd, on the CC
    turn (ceiling_cost). Since fix 6a the NS turns and the router report a cost too; the
    whole of it, engine plus router on every turn, is kept beside as all_turns_usd, for
    information only, and all_turns_partial says some of it went unseen.
    """

    def __init__(self, max_posts: int = MAX_CHAT_POSTS,
                 ceiling_usd: float = SPEND_CEILING_USD) -> None:
        self.max_posts = max_posts
        self.ceiling_usd = ceiling_usd
        self.posts = 0
        self.refused = 0
        self.spent_usd = 0.0
        self.all_turns_usd = 0.0
        self.all_turns_partial = False

    def admit_post(self) -> bool:
        if self.posts >= self.max_posts:
            self.refused += 1
            return False
        self.posts += 1
        return True

    def add_cost(self, usd: float | None) -> None:
        if usd is not None:
            self.spent_usd += float(usd)

    def add_turn_total(self, usd: float | None, *, partial: bool) -> None:
        """One turn's whole cost (turn_total), never counted toward the ceiling."""
        if usd is not None:
            self.all_turns_usd += float(usd)
        self.all_turns_partial = self.all_turns_partial or partial or usd is None

    @property
    def over_ceiling(self) -> bool:
        return self.spent_usd > self.ceiling_usd


def first_event(progress: list, name: str) -> dict | None:
    for entry in progress or []:
        if isinstance(entry, dict) and entry.get("event") == name:
            return entry.get("data") or {}
    return None


def route_decision(progress: list) -> tuple[str | None, str | None]:
    data = first_event(progress, "route_decided") or {}
    return data.get("route"), data.get("source")


def cc_model_id(progress: list) -> str | None:
    data = first_event(progress, "cc_turn_meta")
    return None if data is None else data.get("model_id")


def query_error(progress: list) -> str | None:
    data = first_event(progress, "query_error")
    if data is None:
        return None
    return str(data.get("error") or data)


def is_terminal(status: str | None) -> bool:
    return status in ("completed", "error")


def _usd(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def reported_cost(result: dict | None) -> float | None:
    """The turn's engine cost as its terminal event reports it, CC or NS."""
    if not isinstance(result, dict):
        return None
    return _usd(result.get("total_cost_usd"))


def ceiling_cost(route: str | None, result: dict | None) -> float | None:
    """What the ceiling counts for one turn: Claude Code's own total_cost_usd, on a CC
    turn only. An NS turn reports a cost too since fix 6a (about $0.20 for a graph
    turn), and counting it would change what the $1.00 ceiling means."""
    return reported_cost(result) if route == "container_cc" else None


def router_cost(progress: list) -> float | None:
    """The router's cost as route_decided reports it (fix 6a)."""
    return _usd((first_event(progress, "route_decided") or {}).get("router_cost_usd"))


def turn_total(progress: list, result: dict | None) -> tuple[float | None, bool]:
    """(engine plus router cost, partial) for one turn, for information.

    A part that did not run is not missing: a forced turn made no router call and an
    unrelated turn ran no engine. Any other unreported part, or one that says its figure
    is a floor, makes the total partial; a turn with no part seen has no total.
    """
    decided = first_event(progress, "route_decided") or {}
    end = result if isinstance(result, dict) else {}
    parts: list[float] = []
    partial = bool(end.get("cost_partial") is True or decided.get("router_cost_partial") is True)
    if decided.get("source") != "forced":
        if router_cost(progress) is None:
            partial = True
        else:
            parts.append(router_cost(progress))
    if decided.get("route") != "unrelated":
        if reported_cost(end) is None:
            partial = True
        else:
            parts.append(reported_cost(end))
    return (round(sum(parts), 6) if parts else None), partial


def bundle_path(mode: str | None) -> str:
    """The path a bundle's mode says the turn took: graph for the graph branch,
    api for a search mode, and any other mode under its own name, so a turn that
    lands on reporter (say) fails the path check naming reporter instead of
    passing as api."""
    if mode == GRAPH_MODE:
        return "graph"
    if mode in API_MODES:
        return "api"
    return mode or "no mode"


def observed_path(route: str | None, bundle_id: int | None) -> str | None:
    """The path a completed turn took, where the turn alone says: cc for a CC turn,
    system for an NS turn that registered no bundle (the system agent ends with
    bundle_id=None). A bundle turn's path is its bundle's mode, which only the
    bundle says, so it is None here and the bundle test records it."""
    if route == "container_cc":
        return "cc"
    if route == "nextseek_query" and bundle_id is None:
        return "system"
    return None


def offered_spreadsheets(artifacts) -> list[str]:
    """The keys of the artifacts a turn offered that download as a spreadsheet:
    every table (the page's download button fetches it as xlsx) and every file whose
    format is xlsx. The lane checks exactly these, and asks for no spreadsheet a
    turn did not offer."""
    keys = []
    for art in artifacts or []:
        if not isinstance(art, dict) or not art.get("key"):
            continue
        if art.get("artifact_type") == "table" or art.get("file_format") == "xlsx":
            keys.append(art["key"])
    return keys


def normalize(text: str) -> str:
    """Letters and digits only, lower case, single spaces."""
    return re.sub(r"[^0-9A-Za-z]+", " ", text or "").strip().lower()


def plain_prefix(text: str, n: int = 30) -> str:
    """The first n characters of the reply's first non-empty line, normalized:
    enough to find the same reply once the page has rendered its markdown."""
    for line in (text or "").splitlines():
        cleaned = normalize(line)
        if cleaned:
            return cleaned[:n].strip()
    return ""


@dataclass
class TurnRecord:
    key: str
    text: str
    expected_route: str
    task_id: str | None = None
    session_id: str | None = None
    status: str | None = None
    progress: list = field(default_factory=list)
    result: dict | None = None
    reply: str = ""
    bundle_id: int | None = None
    route: str | None = None
    source: str | None = None
    path: str | None = None        # system | api | graph | cc, as observed (CI record)
    model_id: str | None = None
    cost_usd: float | None = None          # the engine's total_cost_usd, CC or NS
    router_cost_usd: float | None = None   # route_decided's router_cost_usd
    seconds: float | None = None
    error: str | None = None
    debug_agents: list = field(default_factory=list)     # Debug panel entries after the turn
    page_downloads: dict = field(default_factory=dict)   # {"json": filename, "metadata": filename}
    page_artifacts: int = 0                              # artifact links the page rendered


_SUMMARY_FIELDS = ("key", "text", "expected_route", "route", "source", "path", "task_id",
                   "session_id", "status", "seconds", "cost_usd", "router_cost_usd", "error")


def summary_payload(records: list, budget: ChatBudget, kept_session: dict | None,
                    evidence_dir: str | None, *, cleanup_error: str | None = None) -> dict:
    """What the lane reports to the CI record (startup/ci/runner.py renders it).
    cleanup_error says what finish_chat could not title or delete."""
    return {
        "questions": [{k: getattr(r, k) for k in _SUMMARY_FIELDS} for r in records],
        "posts": budget.posts,
        "refused_posts": budget.refused,
        "spent_usd": round(budget.spent_usd, 4),
        "ceiling_usd": budget.ceiling_usd,
        # Every turn's engine plus router cost, for information: not what the ceiling counts.
        "all_turns_usd": round(budget.all_turns_usd, 4),
        "all_turns_partial": budget.all_turns_partial,
        "kept_session": kept_session,
        "evidence_dir": evidence_dir,
        "cleanup_error": cleanup_error,
    }


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

_ADMIN_HELP = ("CI_WRITE_USER (the superuser in ~/.config/nextseek/ci.env) drives the "
               "Nessie lane")


def require_write_creds() -> tuple[str, str]:
    """The write account's credentials, or a failure that names where they go.

    Not conftest's write_creds fixture: that one skips when they are missing, which
    the opt-in write lane relies on. Here a skip would let the whole lane read green
    on a misconfigured box, which decision 6 of the spec rules out.
    """
    creds = _cred(("CI_WRITE_USER", "CI_WRITE_PASS"))
    if creds is None:
        pytest.fail(
            "CI_WRITE_USER and CI_WRITE_PASS are not set in the environment and not in "
            "~/.config/nextseek/ci.env (or the file NEXTSEEK_CI_ENV names). "
            f"{_ADMIN_HELP}, so none of its stages can run.", pytrace=False)
    return creds


@pytest.fixture(scope="module")
def nessie_write_creds() -> tuple[str, str]:
    return require_write_creds()


@pytest.fixture(scope="module")
def nessie_admin_api(profile, base_url, nessie_write_creds) -> GuardedSession:
    """Basic-authenticated client for the write account, cookie-free for the reason
    the write lane's wapi fixture gives: a sessionid would outrank the Basic header."""
    s = GuardedSession(profile=profile, base_url=base_url)
    s.auth = nessie_write_creds
    s.headers["Accept"] = "application/json"
    return s


_SMOKE_HELP = ("CI_SMOKE_USER (the non-superuser in ~/.config/nextseek/ci.env) answers the "
               "Nessie lane's smoke-auth and web-auth checks")


def require_smoke_creds() -> tuple[str, str]:
    """The smoke account's credentials, or a failure that names where they go.

    Not conftest's smoke_creds fixture, nor its api and web clients built on it:
    those skip when the account is missing, so the smoke-auth and web-auth checks
    below would skip and the lane would still exit green on a misconfigured box,
    which decision 6 of the spec rules out.
    """
    creds = _cred(("CI_SMOKE_USER", "CI_SMOKE_PASS"))
    if creds is None:
        pytest.fail(
            "CI_SMOKE_USER and CI_SMOKE_PASS are not set in the environment and not in "
            "~/.config/nextseek/ci.env (or the file NEXTSEEK_CI_ENV names). "
            f"{_SMOKE_HELP}, so they cannot run.", pytrace=False)
    return creds


@pytest.fixture(scope="module")
def nessie_smoke_creds() -> tuple[str, str]:
    return require_smoke_creds()


@pytest.fixture(scope="module")
def nessie_smoke_api(profile, base_url, nessie_smoke_creds) -> GuardedSession:
    """Basic-authenticated client for the smoke account: conftest's api, but failing
    rather than skipping when the account is missing."""
    s = GuardedSession(profile=profile, base_url=base_url)
    s.auth = nessie_smoke_creds
    s.headers["Accept"] = "application/json"
    return s


@pytest.fixture(scope="module")
def nessie_web(profile, base_url, nessie_smoke_creds) -> GuardedSession:
    """Session-cookie client for /seek/* as the smoke account: conftest's web, but
    failing rather than skipping when the account is missing."""
    return web_session(profile, base_url, nessie_smoke_creds)


@pytest.fixture(scope="module")
def nessie_budget() -> ChatBudget:
    return ChatBudget()


@pytest.fixture(scope="module")
def nessie_evidence_dir(tmp_path_factory) -> Path:
    """startup/ci/runner.py names this through CI_NESSIE_EVIDENCE_DIR; a direct
    pytest run gets a temporary directory."""
    raw = os.environ.get("CI_NESSIE_EVIDENCE_DIR")
    path = Path(raw) if raw else tmp_path_factory.mktemp("nessie-evidence")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module", autouse=True)
def nessie_failures_before(request) -> int:
    """How many tests had failed in this session before the lane's first test.

    Autouse and module-scoped, so it is taken at the setup of this module's first
    test, before any of stage 1. Every teardown that keeps evidence compares
    against this one count, so a stage 1 failure keeps the chat, the trace and
    the screenshot even when every turn test passes (spec 3.1, decision 8). A
    count taken in chat_run's own setup would run after the whole of stage 1 and
    miss it.
    """
    return request.session.testsfailed


def lane_failed(request, failures_before: int) -> bool:
    """Has any test failed since the lane started? Failures in modules that ran
    earlier in the same session are not the lane's."""
    return request.session.testsfailed > failures_before


@pytest.fixture(scope="module")
def nessie_context(request, browser, profile, base_url, nessie_write_creds, tmp_path_factory,
                   nessie_budget, nessie_evidence_dir, nessie_failures_before):
    """A browser context logged in as the write account.

    Its network guard admits at most MAX_CHAT_POSTS chat POSTs (a further one is
    aborted and fails the lane) and aborts any other paid POST outright.
    """
    state = login_storage_state(browser, profile, base_url, nessie_write_creds,
                                tmp_path_factory.mktemp("nessie-auth") / "state.json")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                              storage_state=state, base_url=base_url,
                              accept_downloads=True)
    _guard_context(ctx, profile)

    def _guard(route):
        verdict = classify_request(route.request.method, route.request.url)
        if verdict == "blocked" or (verdict == "lane" and not nessie_budget.admit_post()):
            return route.abort()
        return route.continue_()

    ctx.route("**/nextseek_api/**", _guard)
    ctx.tracing.start(screenshots=True, snapshots=True)
    yield ctx
    if lane_failed(request, nessie_failures_before):
        ctx.tracing.stop(path=str(nessie_evidence_dir / "trace.zip"))
    else:
        ctx.tracing.stop()
    ctx.close()


@pytest.fixture(scope="module")
def nessie_page(request, nessie_context, base_url, nessie_evidence_dir, nessie_failures_before):
    page = nessie_context.new_page()
    page.goto(f"{base_url}/seek/assistant/", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_test_id("chat-input").wait_for(state="visible", timeout=60_000)
    yield page
    if lane_failed(request, nessie_failures_before):
        page.screenshot(path=str(nessie_evidence_dir / "page.png"), full_page=True)


# --------------------------------------------------------------------------- #
# stage 1: everything exists (no model call)
# --------------------------------------------------------------------------- #

def test_the_write_credentials_are_present():
    """Checked first, as its own red line: without them every fixture below errors
    with the same message, and this names the cause once."""
    require_write_creds()


def test_the_smoke_credentials_are_present():
    """Its own red line too: the smoke-auth and web-auth checks below need them, and
    without this each of them would error with the same message."""
    require_smoke_creds()


def test_the_write_account_is_an_admin_in_a_participating_project(nessie_admin_api, base_url):
    r = nessie_admin_api.get(f"{base_url}/nextseek_api/assistant/me/", timeout=60)
    assert r.status_code != 403, (
        f"{_ADMIN_HELP}, and it is not in ASSISTANT_PARTICIPATING_PROJECTS "
        "(dmac/local_settings.py), so it cannot send a chat turn")
    assert r.status_code == 200, f"assistant/me answered {r.status_code}: {r.text[:200]}"
    assert r.json().get("is_admin") is True, f"{_ADMIN_HELP}, and it is not a superuser"


def test_the_smoke_account_is_not_an_admin(nessie_smoke_api, base_url):
    r = nessie_smoke_api.get(f"{base_url}/nextseek_api/assistant/me/", timeout=60)
    assert r.status_code == 200, (
        f"assistant/me as the smoke account answered {r.status_code}: {r.text[:200]}")
    assert r.json().get("is_admin") is False, (
        f"assistant/me says the smoke account has is_admin={r.json().get('is_admin')!r}")


def test_the_chat_page_renders_every_control(nessie_page):
    # Imported here, not at module scope: test_nessie_unit.py imports this module
    # in the no-stack lane, which has no playwright.
    from playwright.sync_api import expect

    page = nessie_page
    page.get_by_test_id("new-chat-button").click()   # a fresh chat has no bundle
    for test_id in ("chat-input", "send-button", "new-chat-button", "upload-control"):
        assert page.get_by_test_id(test_id).first.is_visible(), f"{test_id} is not visible"
    assert page.get_by_label("Saved chats").count() >= 1, "the saved-chats sidebar is missing"
    page.get_by_label("Toggle debug panel").click()
    page.get_by_test_id("debug-panel").wait_for(state="visible", timeout=30_000)
    # Waiting assertions from here on. The sheet's controls are not all mounted the
    # moment the panel is: the route override renders only once the page's own
    # assistant/me call has set isAdmin, and an instant is_visible() lost that race
    # on the live stack.
    expect(page.locator("#route-override"),
           "the admin route override is missing for a superuser").to_be_visible(timeout=30_000)
    for test_id in ("json-download", "metadata-download"):
        button = page.get_by_test_id(test_id)
        message = f"{test_id} must be present and disabled before a chat has a bundle"
        expect(button, message).to_be_visible(timeout=30_000)
        expect(button, message).to_be_disabled(timeout=30_000)
    page.keyboard.press("Escape")


NESSIE_ROUTES = [
    r for r in routes.REGISTRY
    if r.path and "GET" in r.methods
    and r.pattern.startswith(tuple(f"^nextseek_api/^^{p}" for p in NESSIE_PREFIXES))
]


@pytest.mark.parametrize("route", NESSIE_ROUTES, ids=lambda r: r.path)
def test_every_nessie_route_answers(route, profile, base_url, anon, nessie_smoke_api, nessie_web,
                                   nessie_admin_api):
    """T0 for the Nessie surface, including the superuser-only routes T0 never
    requests (its sweep must never hold superuser; the pin is
    test_t0_never_sweeps_a_write_auth_route_under_any_profile)."""
    if profile not in route.profiles:
        pytest.skip(f"not enabled for {profile}")
    if "{" in route.path:
        pytest.skip("needs a discovered placeholder; T0 covers it")
    client = {"anon": anon, "smoke": nessie_smoke_api, "web": nessie_web,
              "write": nessie_admin_api}[route.auth]
    r = client.get(f"{base_url}{route.path}", timeout=60, allow_redirects=False)
    expected = route.expect if isinstance(route.expect, tuple) else (route.expect,)
    if route.xfail and r.status_code not in expected:
        pytest.xfail(route.xfail)
    assert r.status_code in expected, (
        f"{route.path} answered {r.status_code}, expected {expected}: {r.text[:200]}")


def test_sessions_test_cases_and_uploads_answer(nessie_admin_api, base_url):
    for path, key in (("assistant/sessions/", "sessions"), ("assistant/test-cases/", "test_cases"),
                      ("nessie/uploads/", "files")):
        r = nessie_admin_api.get(f"{base_url}/nextseek_api/{path}", timeout=60)
        assert r.status_code == 200, f"{path} answered {r.status_code}: {r.text[:200]}"
        assert key in r.json(), f"{path} has no {key!r} key: {list(r.json())[:20]}"


def test_schema_rag_retrieve_returns_endpoints(nessie_smoke_api, base_url):
    """It answers 200 even when retrieval failed, so the list is what is asserted.

    The list is `endpoints_minimal` in the default minimal mode (`endpoints_full` in
    full mode): RetrieveResponse in nextseek_api/models.py has no `endpoints` key.
    """
    body = {**SCHEMA_RAG_QUERY, "schema_url": f"{base_url}{SELF_SCHEMA_PATH}"}
    r = nessie_smoke_api.post(f"{base_url}/nextseek_api/schema_rag/retrieve/", json=body,
                              timeout=120)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    data = r.json()
    assert data.get("endpoints_minimal"), (
        f"no endpoints retrieved: message={data.get('message')!r} "
        f"error_code={(data.get('debug') or {}).get('error_code')!r}")


def test_a_scratch_session_is_created_renamed_and_deleted(nessie_admin_api, base_url):
    root = f"{base_url}/nextseek_api/assistant/sessions/"
    r = nessie_admin_api.post(root, json={}, timeout=60)
    assert r.status_code == 201, f"create: {r.status_code} {r.text[:200]}"
    sid = r.json()["session_id"]
    try:
        r = nessie_admin_api.patch(f"{root}{sid}/", json={"title": "ci nessie scratch"}, timeout=60)
        assert r.status_code == 200, f"rename: {r.status_code} {r.text[:200]}"
        assert r.json().get("title") == "ci nessie scratch", (
            f"rename answered title {r.json().get('title')!r}")
    finally:
        r = nessie_admin_api.delete(f"{root}{sid}/", timeout=60)
    assert r.status_code == 204, f"delete: {r.status_code}"
    gone = nessie_admin_api.get(f"{root}{sid}/", timeout=60)
    assert gone.status_code == 404, (
        f"assistant/sessions/{sid}/ answered {gone.status_code} after the delete, expected 404")


# --------------------------------------------------------------------------- #
# stages 2 and 3: the questions, asked in the page and checked through the API
# --------------------------------------------------------------------------- #

def _poll(api, base_url: str, rec: TurnRecord, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"{base_url}/nextseek_api/nessie/tasks/{rec.task_id}/progress/"
    while True:
        r = api.get(url, timeout=POLL_READ_TIMEOUT_S)
        assert r.status_code == 200, f"{rec.key}: progress answered {r.status_code}"
        body = r.json()
        rec.status, rec.progress, rec.result = body["status"], body["progress"], body.get("result")
        if is_terminal(rec.status):
            return
        if time.monotonic() > deadline:
            rec.error = f"no terminal status after {timeout_s} s (last: {rec.status})"
            return
        time.sleep(POLL_INTERVAL_S)


def _open_debug(page) -> None:
    if not page.get_by_test_id("debug-panel").is_visible():
        page.get_by_label("Toggle debug panel").click()
        page.get_by_test_id("debug-panel").wait_for(state="visible", timeout=30_000)


def _page_download(page, test_id: str, directory: Path) -> str:
    from playwright.sync_api import expect

    button = page.get_by_test_id(test_id)
    # Waiting, not instant: the button enables on the render after query_complete.
    expect(button, f"{test_id} is still disabled after a bundle turn").to_be_enabled(
        timeout=30_000)
    with page.expect_download(timeout=60_000) as got:
        button.click()
    target = directory / got.value.suggested_filename
    got.value.save_as(str(target))
    json.loads(target.read_text())          # it must be JSON
    return got.value.suggested_filename


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: " + " ".join(str(exc).split())[:600]


def _answered(r) -> str:
    body = " ".join((r.text or "").split())[:200]
    return f"{r.status_code}" + (f": {body}" if body else "")


def _session_key(session_id) -> str:
    """One spelling per chat id, so the list's id and the chat POST's compare equal."""
    try:
        return str(uuid.UUID(str(session_id)))
    except ValueError:
        return str(session_id)


def _delete_chat(api, base_url: str, session_id: str) -> str | None:
    """DELETE one chat of the write account; None when it went, else why not."""
    try:
        r = api.delete(f"{base_url}/nextseek_api/assistant/sessions/{session_id}/", timeout=60)
    except Exception as exc:
        return f"the chat {session_id} was not deleted: {_describe(exc)}"
    if r.status_code != 204:
        return f"the chat {session_id} was not deleted: DELETE answered {_answered(r)}"
    return None


def prune_passing_chats(api, base_url: str, kept_id: str) -> list[str]:
    """Delete the write account's passing-lane chats beyond the newest
    KEEP_PASSING_CHATS, `kept_id` (the chat this lane just kept) counted first and
    never deleted. Returns one line per thing that did not work; never raises.

    It finds them by PASSING_CHAT_TITLE in assistant/sessions/, which lists a user's
    newest 50 chats by last update, and orders them by creation. So a passing chat
    pushed past 50 newer chats of the write account (a CI account, so that takes
    months of runs) is not seen, and stays until deleted by hand.
    """
    try:
        r = api.get(f"{base_url}/nextseek_api/assistant/sessions/", timeout=60)
    except Exception as exc:
        return [f"older passing chats were not pruned: {_describe(exc)}"]
    if r.status_code != 200:
        return [f"older passing chats were not pruned: assistant/sessions/ answered "
                f"{_answered(r)}"]
    try:
        rows = r.json().get("sessions")
    except (ValueError, AttributeError) as exc:
        rows, why = None, _describe(exc)
    else:
        why = f"no sessions list in its answer: {' '.join(r.text.split())[:200]}"
    if not isinstance(rows, list):
        return [f"older passing chats were not pruned: assistant/sessions/ gave {why}"]
    keep = _session_key(kept_id)
    older = [row for row in rows
             if isinstance(row, dict) and row.get("session_id")
             and str(row.get("title") or "").startswith(PASSING_CHAT_TITLE)
             and _session_key(row["session_id"]) != keep]
    older.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    errors = []
    for row in older[KEEP_PASSING_CHATS - 1:]:
        error = _delete_chat(api, base_url, row["session_id"])
        if error:
            errors.append(error)
    return errors


def finish_chat(api, base_url: str, session_id: str | None, *, failed: bool,
                evidence_dir: Path) -> tuple[dict | None, str | None]:
    """Decision 8, amended 2026-09-18: keep the chat whether the lane failed or passed.

    A failing lane keeps its chat as it is and saves its /debug/ answer as
    evidence_dir/debug.json. A passing lane keeps its chat too, so that a later
    intermittent failure has a passing chat to be diffed against: it titles it
    PASSING_CHAT_TITLE plus the UTC time, then deletes the older marked chats beyond
    KEEP_PASSING_CHATS (prune_passing_chats). A passing chat that cannot be titled is
    deleted instead, because no later run could find it to prune it.

    Returns (kept, cleanup_error). `kept` names the kept chat and its /debug/ URL.
    `cleanup_error` says what did not work (the title, a DELETE, the sessions list),
    so a leftover chat is reported rather than silent. Never raises: chat_run writes
    the summary after this.
    """
    if not session_id:
        return None, None
    kept = {"session_id": session_id,
            "debug_url": f"{base_url}/nextseek_api/nessie/sessions/{session_id}/debug/"}
    if failed:
        try:
            r = api.get(f"{kept['debug_url']}?include=all", timeout=60)
            (evidence_dir / "debug.json").write_text(r.text)
        except Exception as exc:
            (evidence_dir / "debug.json").write_text(json.dumps({"error": _describe(exc)}))
        return kept, None
    title = f"{PASSING_CHAT_TITLE} {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    try:
        r = api.patch(f"{base_url}/nextseek_api/assistant/sessions/{session_id}/",
                      json={"title": title}, timeout=60)
        untitled = None if r.status_code == 200 else f"the title PATCH answered {_answered(r)}"
    except Exception as exc:
        untitled = f"the title PATCH failed: {_describe(exc)}"
    if untitled:
        errors = [f"the passing chat {session_id} was not kept, since {untitled}"]
        error = _delete_chat(api, base_url, session_id)
        if error:
            errors.append(error)
        return None, "; ".join(errors)
    errors = prune_passing_chats(api, base_url, session_id)
    return kept, "; ".join(errors) or None


def _ask(page, q: Question, rec: TurnRecord, index: int, *, api, base_url: str,
         budget: ChatBudget, downloads: Path) -> None:
    """Type one question in the page, follow its turn through the API, then read
    what the page rendered. Raises on a page or network failure; chat_run records
    that on the turn."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page.get_by_test_id("chat-input").fill(q.text)
    with page.expect_response(
        lambda r: urlsplit(r.url).path == CHAT_PATH and r.request.method == "POST",
        timeout=60_000,
    ) as got:
        page.get_by_test_id("send-button").click()
    assert got.value.status == 202, f"{q.key}: the chat POST answered {got.value.status}"
    body = got.value.json()
    rec.task_id, rec.session_id = body["task_id"], body["session_id"]

    t0 = time.monotonic()
    _poll(api, base_url, rec, TURN_TIMEOUT_S[q.route])
    rec.seconds = round(time.monotonic() - t0, 1)
    rec.route, rec.source = route_decision(rec.progress)
    rec.model_id = cc_model_id(rec.progress)
    rec.error = rec.error or query_error(rec.progress)
    if isinstance(rec.result, dict):
        rec.reply = rec.result.get("reply") or ""
        rec.bundle_id = rec.result.get("bundle_id")
    rec.cost_usd = reported_cost(rec.result)
    rec.router_cost_usd = router_cost(rec.progress)
    budget.add_cost(ceiling_cost(rec.route, rec.result))
    total, partial = turn_total(rec.progress, rec.result)
    budget.add_turn_total(total, partial=partial)
    if rec.error or rec.status != "completed":
        return            # no reply to render; the per-question test names the cause
    rec.path = observed_path(rec.route, rec.bundle_id)

    # The page must render the reply before the next question is typed.
    bubble = page.locator('[data-testid="message-bubble"][data-role="assistant"]').nth(index)
    bubble.wait_for(state="visible", timeout=60_000)
    _open_debug(page)
    # The page empties the Debug panel on every send (handleSendMessage in
    # NessieAI/chat_frontend/src/EmbeddedApp.tsx), so these are this turn's entries only.
    rec.debug_agents = [
        e.get_attribute("data-agent")
        for e in page.get_by_test_id("debug-entry").all()
    ]
    if q.bundle and rec.bundle_id is not None:
        # CC turns carry no bundle, so these buttons are checked right after a
        # bundle turn, before the next turn can disable them.
        rec.page_downloads = {
            "json": _page_download(page, "json-download", downloads),
            "metadata": _page_download(page, "metadata-download", downloads),
        }
    page.keyboard.press("Escape")
    if q.cc_artifact:
        # Counted inside this turn's own bubble: the NS turns above it render
        # artifact-download buttons of their own. The links arrive on the render
        # after the reply (updateLastAssistantMessage runs in a microtask).
        links = bubble.get_by_test_id("artifact-download")
        try:
            links.first.wait_for(state="visible", timeout=30_000)
        except PlaywrightTimeout:
            pass                             # none rendered: the CC test reports it
        rec.page_artifacts = links.count()


@pytest.fixture(scope="module")
def chat_run(request, nessie_page, nessie_admin_api, base_url, nessie_budget,
             nessie_evidence_dir, tmp_path_factory, nessie_failures_before):
    if request.config.getoption("--nessie-no-turns"):
        pytest.skip("chat turns skipped by --nessie-no-turns")
    page = nessie_page
    downloads = tmp_path_factory.mktemp("nessie-downloads")
    records: list[TurnRecord] = []
    started = time.monotonic()

    for index, q in enumerate(QUESTIONS):
        rec = TurnRecord(key=q.key, text=q.text, expected_route=q.route)
        records.append(rec)
        if time.monotonic() - started >= LANE_DEADLINE_S:
            rec.error = f"not asked: the lane passed its {LANE_DEADLINE_S} s deadline"
            break
        try:
            if index == 0:
                page.get_by_test_id("new-chat-button").click()   # one fresh chat for all four
            _ask(page, q, rec, index, api=nessie_admin_api, base_url=base_url,
                 budget=nessie_budget, downloads=downloads)
        except Exception as exc:
            # Recorded on its turn rather than raised: an exception here would error
            # every test with the same traceback and skip the teardown below, which
            # is what keeps the chat, the evidence and the summary.
            rec.error = rec.error or _describe(exc)
        if rec.error or rec.status != "completed":
            break                            # later questions would only add noise

    yield records

    # Against the count taken before stage 1, not at this fixture's setup: a
    # stage 1 failure must keep the chat too (nessie_failures_before).
    failed = lane_failed(request, nessie_failures_before)
    session_id = next((r.session_id for r in records if r.session_id), None)
    kept, cleanup_error = finish_chat(nessie_admin_api, base_url, session_id,
                                      failed=failed, evidence_dir=nessie_evidence_dir)
    if cleanup_error:
        # The CI record carries it through the summary; a direct pytest run has
        # no record, so it also lands in the warnings summary.
        warnings.warn(f"Nessie lane cleanup: {cleanup_error}", stacklevel=1)
    target = os.environ.get("CI_NESSIE_SUMMARY")
    if target:
        Path(target).write_text(json.dumps(
            summary_payload(records, nessie_budget, kept,
                            str(nessie_evidence_dir) if failed else None,
                            cleanup_error=cleanup_error),
            indent=2))


def _rec(records: list, key: str) -> TurnRecord:
    for rec in records:
        if rec.key == key:
            return rec
    pytest.fail(f"{key} was never asked: an earlier question failed, see its test")


def _completed(records: list, key: str) -> TurnRecord:
    rec = _rec(records, key)
    if rec.error or rec.status != "completed":
        pytest.fail(f"{key} did not complete ({rec.error or rec.status}), see its test")
    return rec


turn = pytest.mark.nessie_turn
# Each per-question check is parametrized over the rows it applies to, so a new
# row gets every check its kind has (test_nessie_unit.py pins this).
SYSTEM_QUESTIONS = [q for q in QUESTIONS if q.path == "system"]
BUNDLE_QUESTIONS = [q for q in QUESTIONS if q.bundle]
CC_QUESTIONS = [q for q in QUESTIONS if q.route == "container_cc"]
#: The one warning a healthy mixed chat raises: session_debug._warnings compares
#: bundles with chat_log entries, and the system answer and the CC turn write a
#: chat_log entry but no bundle.
BUNDLE_COUNT_WARNING = "bundle_chatlog_count_mismatch"


@turn
@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q.key)
def test_each_question_completes_on_its_engine_through_the_router(q, chat_run):
    rec = _rec(chat_run, q.key)
    assert rec.error is None, f"{q.key}: {rec.error}"
    assert rec.status == "completed", f"{q.key}: status {rec.status}"
    assert (rec.route, rec.source) == (q.route, "baml"), (
        f"{q.key}: routed to {rec.route} by {rec.source}; expected {q.route} by baml. "
        "A 'heuristic' source means the BAML router is not answering.")
    assert rec.reply.strip(), f"{q.key}: empty reply"


@turn
def test_every_turn_rendered_its_reply_and_its_route_entry(chat_run, nessie_page):
    bubbles = nessie_page.locator('[data-testid="message-bubble"][data-role="assistant"]')
    assert bubbles.count() >= len(chat_run), (
        f"the page shows {bubbles.count()} replies for {len(chat_run)} questions")
    for i, rec in enumerate(chat_run):
        _completed(chat_run, rec.key)
        assert plain_prefix(rec.reply) in normalize(bubbles.nth(i).inner_text()), (
            f"{rec.key}: the page does not show the reply the API returned")
        assert rec.debug_agents.count(ROUTE_ENTRY_AGENT) >= 1, (
            f"{rec.key}: the Debug panel has no route entry for this turn "
            f"(entries: {rec.debug_agents})")


@turn
@pytest.mark.parametrize("q", SYSTEM_QUESTIONS, ids=lambda q: q.key)
def test_each_system_answer_registers_no_bundle(q, chat_run):
    rec = _completed(chat_run, q.key)
    assert rec.bundle_id is None, (
        f"{q.key}: the system answer registered bundle {rec.bundle_id}; the system "
        "agent path ends with bundle_id=None")


@turn
@pytest.mark.parametrize("q", BUNDLE_QUESTIONS, ids=lambda q: q.key)
def test_bundle_turns_download_and_took_the_expected_path(q, chat_run, nessie_admin_api, base_url):
    rec = _completed(chat_run, q.key)
    assert isinstance(rec.bundle_id, int), f"{q.key}: no bundle registered"
    root = f"{base_url}/nextseek_api/assistant/sessions/{rec.session_id}/bundles/{rec.bundle_id}/"
    r = nessie_admin_api.get(root, timeout=60)
    assert r.status_code == 200, f"{q.key}: the bundle JSON answered {r.status_code}"
    bundle = r.json()
    # Recorded on the turn for the CI record's path column: chat_run's teardown
    # writes the summary after every test in this module has run.
    rec.path = bundle_path(bundle.get("mode"))
    assert rec.path == q.path, (
        f"{q.key}: the bundle was built by the {rec.path} path "
        f"(mode {bundle.get('mode')!r}); expected {q.path}")
    meta = nessie_admin_api.get(f"{root}?part=metadata", timeout=60)
    assert meta.status_code == 200, (
        f"{q.key}: the bundle's ?part=metadata answered {meta.status_code}: {meta.text[:200]}")
    assert "omitted" in meta.json(), (
        f"{q.key}: the bundle's ?part=metadata has no 'omitted' key: {list(meta.json())[:20]}")
    if q.path == "api":
        assert "api_result_full" in bundle, "the API bundle lacks the full API result"
    # Every spreadsheet the turn offered (a table, or an xlsx file) must download as
    # one. Nothing is asked for that the turn did not offer: the 2026-09-11 run's
    # NDMA search offered only its full-result JSON, and search_results itself is
    # pinned by NessieAI/tests/api/test_excel_export.py.
    for key in offered_spreadsheets((rec.result or {}).get("artifacts")):
        x = nessie_admin_api.get(f"{root}artifacts/{key}/", timeout=120)
        assert x.status_code == 200, (
            f"{q.key}: the offered spreadsheet {key} answered {x.status_code}: {x.text[:200]}")
        assert "spreadsheet" in x.headers.get("Content-Type", ""), (
            f"{q.key}: the offered spreadsheet {key} came back as "
            f"{x.headers.get('Content-Type')!r}")
        assert len(x.content) > 0, f"{q.key}: the offered spreadsheet {key} downloaded empty"
    # Every file the turn offered the page downloads through the same route.
    for art in (rec.result or {}).get("artifacts") or []:
        if art.get("artifact_type") != "file":
            continue
        key = art["key"]
        assert re.fullmatch(r"\w+", key), (
            f"{q.key}: artifact key {key!r} is not word characters, so its download "
            "URL cannot resolve")
        got = nessie_admin_api.get(f"{root}artifacts/{key}/", timeout=120)
        assert got.status_code == 200, f"{q.key}: artifact {key} answered {got.status_code}"
        assert len(got.content) > 0, f"{q.key}: artifact {key} downloaded empty"
    assert set(rec.page_downloads) == {"json", "metadata"}, (
        f"{q.key}: the page's JSON and Metadata downloads did not both work")


def cc_turn_cap_usd(seconds: float | None) -> float:
    """The most a CC turn of this length may report: CC_COST_PER_MINUTE_USD for
    each minute it ran, with a one-minute floor (an unknown length gets the floor)."""
    minutes = max(1.0, (seconds or 0.0) / 60.0)
    return round(CC_COST_PER_MINUTE_USD * minutes, 4)


@turn
@pytest.mark.parametrize("q", CC_QUESTIONS, ids=lambda q: q.key)
def test_each_cc_turn_has_a_model_artifacts_and_a_bounded_cost(q, chat_run, nessie_admin_api,
                                                               base_url):
    rec = _completed(chat_run, q.key)
    assert rec.model_id, f"{q.key}: cc_turn_meta.model_id is null, so the proxy will answer 403"
    assert rec.cost_usd is not None, f"{q.key}: the CC turn reported no total_cost_usd"
    cap = cc_turn_cap_usd(rec.seconds)
    assert 0 < rec.cost_usd <= cap, (
        f"{q.key}: reported CC cost ${rec.cost_usd} over {rec.seconds} s is outside (0, {cap}] "
        f"(${CC_COST_PER_MINUTE_USD} a minute, one-minute floor)")
    if not q.cc_artifact:
        return
    # A turn that wrote one file lists that file; one that wrote several lists only
    # their <turn_id>/artifacts.zip (_publish_artifacts in NessieAI/cc/cc_engine.py). Either key
    # names a real file under the turn's directory.
    files = [a for a in (rec.result or {}).get("artifacts") or []
             if a.get("artifact_type") == "file"]
    assert files, f"{q.key}: the CC turn left no artifact"
    assert rec.page_artifacts >= 1, f"{q.key}: the page rendered no artifact link for the CC turn"
    key = files[0]["key"]
    turn_id = key.split("/", 1)[0]
    root = f"{base_url}/nextseek_api/cc-assistant/artifacts/{rec.session_id}/download/"
    one = nessie_admin_api.get(root, params={"key": key}, timeout=120)
    assert one.status_code == 200, f"{q.key}: the one-file download of {key} answered {one.status_code}"
    assert len(one.content) > 0, f"{q.key}: the one-file download of {key} was empty"
    zipped = nessie_admin_api.get(root, params={"key": "all", "turn_id": turn_id}, timeout=120)
    assert zipped.status_code == 200, (
        f"{q.key}: the zip of turn {turn_id} answered {zipped.status_code}")
    content_type = zipped.headers.get("Content-Type", "")
    assert "zip" in content_type, f"{q.key}: the zip of turn {turn_id} came back as {content_type!r}"
    alias = nessie_admin_api.get(
        f"{base_url}/nextseek_api/nessie/sessions/{rec.session_id}/artifacts/",
        params={"key": key}, timeout=120)
    assert alias.status_code == 200, f"{q.key}: the nessie artifacts alias answered {alias.status_code}"


@turn
def test_the_session_detail_matches_the_page(chat_run, nessie_admin_api, base_url):
    sid = chat_run[0].session_id
    assert {r.session_id for r in chat_run} == {sid}, "the questions did not share one chat"
    r = nessie_admin_api.get(f"{base_url}/nextseek_api/assistant/sessions/{sid}/",
                             params={"include": "turns"}, timeout=60)
    assert r.status_code == 200, (
        f"assistant/sessions/{sid}/?include=turns answered {r.status_code}: {r.text[:200]}")
    turns = r.json()["turns"]
    assert len(turns) == len(QUESTIONS), f"{len(turns)} turns, expected {len(QUESTIONS)}"
    for t, rec in zip(turns, chat_run):
        assert t["user_query"] == rec.text, (
            f"{rec.key}: the session stores the question {t['user_query']!r}, "
            f"the page sent {rec.text!r}")
        # The NS writer stores the reply cut at its "**Debug info**" block
        # (chat_memory._strip_debug_block); the CC writer stores it whole.
        stored = t["reply"].strip()
        assert stored, f"{rec.key}: the session stores an empty reply"
        assert rec.reply.strip().startswith(stored), (
            f"{rec.key}: the stored reply is not the reply the API returned "
            f"(stored {stored[:80]!r})")
        if rec.bundle_id is not None:
            assert t["bundle_id"] == rec.bundle_id, (
                f"{rec.key}: the session stores bundle {t['bundle_id']}, "
                f"the turn registered {rec.bundle_id}")


@turn
def test_the_debug_endpoint_reports_the_session_and_resolves_a_task(chat_run, nessie_admin_api, base_url):
    sid = chat_run[0].session_id
    debug = f"nessie/sessions/{sid}/debug/"
    r = nessie_admin_api.get(f"{base_url}/nextseek_api/{debug}",
                             params={"include": "transcripts"}, timeout=60)
    assert r.status_code == 200, f"{debug} answered {r.status_code}: {r.text[:200]}"
    d = r.json()
    assert d["resolved_as"] == "session", f"{debug} resolved as {d['resolved_as']!r}"
    counts = d["session"]["counts"]
    for name in ("tasks", "ledger_turns", "chat_log_entries"):
        assert counts[name] == len(QUESTIONS), (
            f"{debug} counts {counts[name]} {name}, expected {len(QUESTIONS)}: {counts}")
    assert len(d["turns"]) == len(QUESTIONS), (
        f"{debug} lists {len(d['turns'])} turns, expected {len(QUESTIONS)}")
    assert len(d["tasks"]) == len(QUESTIONS), (
        f"{debug} lists {len(d['tasks'])} tasks, expected {len(QUESTIONS)}")
    ledger = [row["route"] for row in d["ledger"]]
    assert ledger == [q.route for q in QUESTIONS], (
        f"{debug} route ledger reads {ledger}, expected {[q.route for q in QUESTIONS]}")
    sources = [row["route_source"] for row in d["ledger"]]
    assert all(source == "baml" for source in sources), (
        f"{debug} route ledger sources are {sources}, expected every one baml")
    if BUNDLE_QUESTIONS:
        # session_debug lists the files the session's bundles recorded.
        assert d["files"], f"{debug} lists no files, though a bundle turn ran"
    bundled = sum(1 for rec in chat_run if rec.bundle_id is not None)
    expected = {BUNDLE_COUNT_WARNING} if counts["bundles"] == bundled < len(QUESTIONS) else set()
    unexpected = [w for w in d["warnings"] if w.get("code") not in expected]
    assert not unexpected, f"warnings: {unexpected}"
    task_debug = f"nessie/sessions/{chat_run[-1].task_id}/debug/"
    t = nessie_admin_api.get(f"{base_url}/nextseek_api/{task_debug}", timeout=60)
    assert t.status_code == 200, f"{task_debug} answered {t.status_code}: {t.text[:200]}"
    assert t.json()["resolved_as"] == "task", (
        f"{task_debug} resolved as {t.json()['resolved_as']!r}, expected task")
    if not CC_QUESTIONS:
        return
    assert d["transcripts"], f"{debug} lists no CC transcript, though a CC turn ran"
    transcript = d["transcripts"][0]
    got = nessie_admin_api.get(f"{base_url}{transcript['url']}", timeout=60)
    assert got.status_code == 200, f"{transcript['url']} answered {got.status_code}"
    content_type = got.headers.get("Content-Type", "")
    assert "ndjson" in content_type, (
        f"{transcript['url']} came back as {content_type!r}, not ndjson")
    alias = nessie_admin_api.get(
        f"{base_url}/nextseek_api/nessie/sessions/{sid}/transcript/{transcript['turn_id']}/",
        params={"cc_session_id": transcript["cc_session_id"]}, timeout=60)
    assert alias.status_code == 200, f"nessie transcript alias: {alias.status_code}"


@turn
def test_the_reopened_chat_shows_every_turn(chat_run, nessie_page):
    from playwright.sync_api import expect

    sid = chat_run[0].session_id
    page = nessie_page
    page.reload(wait_until="domcontentloaded")
    item = page.locator(f'[data-testid="session-item"][data-session-id="{sid}"]')
    expect(item, "the chat is not in the saved-chats sidebar after a reload").to_be_visible(
        timeout=60_000)
    item.click()
    bubbles = page.locator('[data-testid="message-bubble"][data-role="assistant"]')
    bubbles.nth(len(QUESTIONS) - 1).wait_for(state="visible", timeout=60_000)
    assert bubbles.count() == len(QUESTIONS), (
        f"the reopened chat shows {bubbles.count()} replies, expected {len(QUESTIONS)}")
    # Each turn's debug detail is rebuilt from the server: NS bundle turns from the
    # bundle's plans, the CC turn from its cc_traces (hydrateFromTurns in
    # NessieAI/chat_frontend/src/hooks/useMessages.ts). The Debug panel itself shows only the
    # newest turn (debugForTurns), and a CC turn legitimately has no entries there.
    # A CC turn's details come from its cc_traces, not from its artifacts
    # (hasCcTrace in MessageBubble.tsx), so CC rows are selected by route.
    for i, q in enumerate(QUESTIONS):
        if q.bundle or q.route == "container_cc":
            expect(bubbles.nth(i).get_by_role("button", name="Search Details"),
                   f"{q.key}: the reopened turn has no Search Details").to_be_visible(
                timeout=30_000)
    _open_debug(page)
    page.keyboard.press("Escape")


@turn
def test_spend_stayed_under_the_ceiling(chat_run, nessie_budget):
    all_turns = (f"all turns with NS and router: {'at least ' if nessie_budget.all_turns_partial else ''}"
                 f"${nessie_budget.all_turns_usd:.2f}")
    # For information, beside the ceiling (shown with -s or -rP; the CI record has it too).
    print(f"Nessie lane spend: ${nessie_budget.spent_usd:.2f} reported by Claude Code on the CC turn, "
          f"ceiling ${SPEND_CEILING_USD:.2f}; {all_turns}")
    assert nessie_budget.refused == 0, "the page tried to send more chat POSTs than questions"
    assert not nessie_budget.over_ceiling, (
        f"reported spend ${nessie_budget.spent_usd:.2f} is over ${SPEND_CEILING_USD:.2f} ({all_turns})")
