"""No-stack tests for the Nessie lane: its selection switches and its pure helpers.

Stack-free by design, like the other *_unit.py files: no network, no credentials,
no browser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.conftest import nessie_skip_reason


@pytest.mark.parametrize("keywords, no_nessie, no_turns, expected", [
    ({"test_x"}, True, True, None),                               # not a Nessie test
    ({"nessie"}, False, False, None),                             # the lane runs
    ({"nessie"}, True, False, "Nessie lane skipped by --no-nessie"),
    ({"nessie", "nessie_turn"}, True, True, "Nessie lane skipped by --no-nessie"),
    ({"nessie", "nessie_turn"}, False, True, "chat turns skipped by --nessie-no-turns"),
    ({"nessie"}, False, True, None),                              # stage 1 still runs
])
def test_nessie_skip_reason(keywords, no_nessie, no_turns, expected):
    assert nessie_skip_reason(keywords, no_nessie=no_nessie, no_turns=no_turns) == expected


class _Config:
    """What pytest_collection_modifyitems reads from a config: three options and
    the memoised profile (set, so resolve_profile reads no environment)."""

    def __init__(self, *, m: str = "", no_nessie: bool = False, no_turns: bool = False):
        self._nextseek_profile = "local"
        self._options = {"-m": m, "--no-nessie": no_nessie, "--nessie-no-turns": no_turns}

    def getoption(self, name):
        return self._options[name]


class _Item:
    def __init__(self, *keywords: str):
        self.keywords = dict.fromkeys(keywords, True)
        self.skip_reasons: list[str] = []

    def get_closest_marker(self, name):
        return None                       # no profiles marker: the profile gate passes

    def add_marker(self, mark):
        assert mark.name == "skip", mark
        self.skip_reasons.append(mark.kwargs["reason"])


WRITE_SKIP = "write lane is opt-in: run with -m write"
NESSIE_SKIP = "Nessie lane skipped by --no-nessie"


def _collect(config: _Config) -> dict[str, list[str]]:
    from ci.smoke.conftest import pytest_collection_modifyitems
    items = {"write": _Item("write"), "nessie": _Item("nessie"),
             "turn": _Item("nessie", "nessie_turn"), "plain": _Item("test_x")}
    pytest_collection_modifyitems(config, list(items.values()))
    return {name: item.skip_reasons for name, item in items.items()}


def test_no_nessie_skips_the_lane_and_keeps_the_write_lane_deselected():
    """Spec 5: --no-nessie selection, including that the write lane stays out."""
    got = _collect(_Config(no_nessie=True))
    assert got["write"] == [WRITE_SKIP], got
    assert got["nessie"] == [NESSIE_SKIP], got
    assert got["turn"] == [NESSIE_SKIP], got
    assert got["plain"] == [], got


def test_nessie_no_turns_skips_only_the_turns_and_keeps_the_write_lane_deselected():
    got = _collect(_Config(no_turns=True))
    assert got["write"] == [WRITE_SKIP], got
    assert got["nessie"] == [], got
    assert got["turn"] == ["chat turns skipped by --nessie-no-turns"], got


def test_a_mark_expression_re_admits_the_write_lane_and_the_nessie_switch_still_holds():
    """The landmine in ci/CLAUDE.md, pinned: any -m (here the tempting
    "not nessie") leaves the write item unskipped, so the superuser write lane
    runs. --no-nessie is applied before that early return and still skips."""
    got = _collect(_Config(m="not nessie", no_nessie=True))
    assert got["write"] == [], got
    assert got["nessie"] == [NESSIE_SKIP], got
    assert got["turn"] == [NESSIE_SKIP], got


def test_the_nessie_switch_is_not_a_mark_expression():
    """-m re-admits the write lane; the Nessie switch must never need one."""
    import ci.smoke.conftest as conftest
    source = Path(conftest.__file__).read_text()
    gate = source.index("def pytest_collection_modifyitems")
    early_return = source.index('if config.getoption("-m"):', gate)
    nessie_gate = source.index("nessie_skip_reason(", gate)
    assert nessie_gate < early_return, (
        "the Nessie gate must run before the -m early return, or --no-nessie "
        "stops working whenever someone passes -m"
    )


import json

from ci.smoke.test_nessie import (
    CHAT_PATH, MAX_CHAT_POSTS, QUESTIONS, SPEND_CEILING_USD, ChatBudget, TurnRecord,
    bundle_path, cc_model_id, ceiling_cost, classify_request, finish_chat, is_terminal, normalize,
    observed_path, offered_spreadsheets, plain_prefix, query_error, reported_cost,
    require_smoke_creds,
    require_write_creds, route_decision, router_cost, summary_payload, turn_total,
)

# pytester runs the lane's real chat_run fixture in a throwaway session, to pin
# which failures keep the chat (the cleanup tests at the end of this file).
pytest_plugins = ["pytester"]

BASE = "http://127.0.0.1:8000"


def test_the_questions_are_three_ns_then_one_cc_with_unique_keys():
    assert [q.route for q in QUESTIONS] == [
        "nextseek_query", "nextseek_query", "nextseek_query", "container_cc"]
    assert [q.path for q in QUESTIONS] == ["system", "graph", "graph", "cc"]  # NDMA moved to the graph in c241c6e6
    assert len({q.key for q in QUESTIONS}) == len(QUESTIONS)
    assert MAX_CHAT_POSTS == len(QUESTIONS) == 4
    assert SPEND_CEILING_USD == 1.00


def test_classify_request():
    assert classify_request("POST", BASE + CHAT_PATH) == "lane"
    assert classify_request("GET", BASE + CHAT_PATH) == "pass"
    assert classify_request("POST", BASE + "/nextseek_api/nessie/query/") == "blocked"
    assert classify_request("POST", BASE + "/nextseek_api/cc-assistant/cc/query/async/") == "blocked"
    assert classify_request("POST", BASE + "/nextseek_api/assistant/sessions/") == "pass"
    assert classify_request("POST", BASE + "/login/") == "pass"


def test_chat_budget_refuses_the_fifth_post_and_tracks_spend():
    b = ChatBudget()
    assert [b.admit_post() for _ in range(5)] == [True, True, True, True, False]
    assert (b.posts, b.refused) == (4, 1)
    b.add_cost(0.24)
    b.add_cost(None)
    assert b.spent_usd == pytest.approx(0.24) and not b.over_ceiling
    b.add_cost(0.80)
    assert b.over_ceiling


def test_the_ceiling_counts_claude_codes_own_cost_on_cc_turns_only():
    """Since fix 6a an NS turn reports a cost too (about $0.20 a graph turn). The $1.00
    ceiling keeps its meaning: Claude Code's reported cost, on the CC turn."""
    assert ceiling_cost("container_cc", {"total_cost_usd": 0.31}) == 0.31
    assert ceiling_cost("nextseek_query", {"total_cost_usd": 0.20}) is None
    assert ceiling_cost("container_cc", {"reply": "x"}) is None
    b = ChatBudget()
    for route, cost in (("nextseek_query", 0.2), ("nextseek_query", 0.2), ("nextseek_query", 0.2),
                        ("container_cc", 0.5)):
        b.add_cost(ceiling_cost(route, {"total_cost_usd": cost}))
    assert b.spent_usd == pytest.approx(0.5) and not b.over_ceiling


def _turn(route="nextseek_query", source="baml", router=0.003, router_partial=False, **end):
    rd = {"route": route, "source": source}
    if router is not None:
        rd.update(router_cost_usd=router, router_cost_partial=router_partial)
    return [{"event": "route_decided", "data": rd}], end or None


def test_a_turns_total_is_its_engine_and_router_cost():
    progress, result = _turn(total_cost_usd=0.2, cost_partial=False)
    assert router_cost(progress) == 0.003
    assert turn_total(progress, result) == (pytest.approx(0.203), False)


@pytest.mark.parametrize("progress_result, partial", [
    (_turn(total_cost_usd=0.2, cost_partial=True), True),                 # the engine says it is a floor
    (_turn(router_partial=True, total_cost_usd=0.2), True),               # the router says so
    (_turn(router=None, total_cost_usd=0.2), True),                       # the router part went unseen
    (_turn(reply="x"), True),                                             # the engine part went unseen
    (_turn(source="forced", router=None, total_cost_usd=0.2), False),     # a forced turn has no router part
    (_turn(route="unrelated", reply="x"), False),                         # an unrelated turn has no engine part
])
def test_a_turns_total_is_partial_when_a_part_that_ran_was_not_seen(progress_result, partial):
    progress, result = progress_result
    assert turn_total(progress, result)[1] is partial


def test_the_budget_keeps_the_all_turns_total_beside_the_ceiling():
    b = ChatBudget()
    b.add_turn_total(0.203, partial=False)
    b.add_turn_total(None, partial=True)
    b.add_turn_total(0.5, partial=False)
    assert b.all_turns_usd == pytest.approx(0.703) and b.all_turns_partial is True
    assert b.spent_usd == 0.0 and not b.over_ceiling, "the all-turns total never counts toward the ceiling"


PROGRESS = [
    {"event": "route_decided", "data": {"route": "container_cc", "source": "baml"}},
    {"event": "cc_turn_meta", "data": {"model_id": "us.anthropic.claude-opus-4-8"}},
    {"event": "query_complete", "data": {"reply": "done"}},
]


def test_progress_parsers():
    assert route_decision(PROGRESS) == ("container_cc", "baml")
    assert route_decision([]) == (None, None)
    assert cc_model_id(PROGRESS) == "us.anthropic.claude-opus-4-8"
    assert cc_model_id([]) is None
    assert query_error(PROGRESS) is None
    assert query_error([{"event": "query_error", "data": {"error": "403 path not permitted"}}]) \
        == "403 path not permitted"


def test_status_cost_and_path_helpers():
    assert is_terminal("completed") and is_terminal("error")
    assert not is_terminal("running") and not is_terminal(None)
    assert reported_cost({"total_cost_usd": 0.21}) == 0.21
    assert reported_cost({"reply": "x"}) is None and reported_cost(None) is None


def test_bundle_path_names_an_unexpected_mode_instead_of_calling_it_api():
    """Only the two search modes are the API path (search_results answers only
    them, services/assistant.py). Any other mode is reported under its own name,
    so a flip to reporter or a submission fails the path check saying which."""
    assert bundle_path("graph_query") == "graph"
    assert bundle_path("new_search") == "api"
    assert bundle_path("refine_last_search") == "api"
    assert bundle_path("reporter") == "reporter"
    assert bundle_path("generate_submission") == "generate_submission"
    assert bundle_path("") == "no mode"
    assert bundle_path(None) == "no mode"


def _parametrized_rows(test_function) -> list:
    marks = [m for m in getattr(test_function, "pytestmark", []) if m.name == "parametrize"]
    assert len(marks) == 1, f"{test_function.__name__} is not parametrized once: {marks}"
    return list(marks[0].args[1])


@pytest.mark.parametrize("test_name, kind", [
    ("test_each_question_completes_on_its_engine_through_the_router", lambda q: True),
    ("test_each_system_answer_registers_no_bundle", lambda q: q.path == "system"),
    ("test_bundle_turns_download_and_took_the_expected_path", lambda q: q.bundle),
    ("test_each_cc_turn_has_a_model_artifacts_and_a_bounded_cost",
     lambda q: q.route == "container_cc"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_every_per_question_check_covers_every_row_of_its_kind(test_name, kind):
    """"A new question is a new row" (spec 3.1): each per-question check is
    parametrized over every row it applies to, never tied to one key or to the
    first match, so a second system or CC row gets every check too."""
    import ci.smoke.test_nessie as nessie
    assert hasattr(nessie, test_name), f"{test_name} is not in test_nessie.py"
    got = [q.key for q in _parametrized_rows(getattr(nessie, test_name))]
    assert got == [q.key for q in QUESTIONS if kind(q)], f"{test_name} covers {got}"


def test_observed_path_of_a_turn_without_a_bundle():
    """The CI record's path column. A CC turn took the cc path; an NS turn that
    registered no bundle took the system path (the system agent ends with
    bundle_id=None); a bundle turn's path is its bundle's mode, which the bundle
    test reads, so it is not guessed here."""
    assert observed_path("container_cc", None) == "cc"
    assert observed_path("nextseek_query", None) == "system"
    assert observed_path("nextseek_query", 7) is None
    assert observed_path(None, None) is None


def test_reply_matching_survives_markdown():
    reply = "**NDMA-treated mice**: 12 found\n\n| id | sex |"
    assert plain_prefix(reply) == "ndma treated mice 12 found"
    assert plain_prefix(reply) in normalize("NDMA-treated mice: 12 found  | id | sex |")
    assert plain_prefix("") == ""


def test_summary_payload_shape():
    rec = TurnRecord(key="nhp_graph", text="t", expected_route="container_cc",
                     route="container_cc", source="baml", task_id="a", session_id="s",
                     status="completed", seconds=88.0, cost_usd=0.24, path="cc")
    b = ChatBudget()
    b.admit_post()
    b.add_cost(0.24)
    out = summary_payload([rec], b, None, "/tmp/e")
    assert out["posts"] == 1 and out["spent_usd"] == 0.24 and out["ceiling_usd"] == 1.0
    assert out["all_turns_usd"] == 0.0 and out["all_turns_partial"] is False
    b.add_turn_total(0.4312345, partial=True)
    out = summary_payload([rec], b, None, "/tmp/e")
    assert out["all_turns_usd"] == 0.4312 and out["all_turns_partial"] is True
    assert "router_cost_usd" in out["questions"][0]
    assert out["questions"][0]["key"] == "nhp_graph"
    assert out["questions"][0]["expected_route"] == "container_cc"
    # Spec 3.4: the record names the path each question took.
    assert out["questions"][0]["path"] == "cc"
    assert out["kept_session"] is None and out["evidence_dir"] == "/tmp/e"


def test_missing_write_credentials_fail_the_lane_and_never_skip(monkeypatch, tmp_path):
    """Decision 6: a misconfigured box fails and names the cause. A skip would let
    the whole lane read green on a box whose ci.env has no write account."""
    monkeypatch.delenv("CI_WRITE_USER", raising=False)
    monkeypatch.delenv("CI_WRITE_PASS", raising=False)
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "absent.env"))
    # Skipped is not a subclass of Failed, so pytest.raises(pytest.fail.Exception)
    # would let a skip escape and report this pin itself as skipped: green again.
    try:
        require_write_creds()
    except pytest.skip.Exception:
        pytest.fail("require_write_creds skipped; a missing write account must fail "
                    "the lane, never skip it")
    except pytest.fail.Exception as failed:
        message = str(failed)
    else:
        pytest.fail("require_write_creds returned with no credentials set anywhere")
    for name in ("CI_WRITE_USER", "CI_WRITE_PASS", "ci.env"):
        assert name in message, f"the failure does not name {name}: {message}"


def test_write_credentials_come_from_the_environment_or_the_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CI_WRITE_USER", raising=False)
    monkeypatch.delenv("CI_WRITE_PASS", raising=False)
    env_file = tmp_path / "ci.env"
    env_file.write_text("CI_WRITE_USER=file-user\nCI_WRITE_PASS=file-pass\n")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(env_file))
    assert require_write_creds() == ("file-user", "file-pass")
    monkeypatch.setenv("CI_WRITE_USER", "env-user")
    monkeypatch.setenv("CI_WRITE_PASS", "env-pass")
    assert require_write_creds() == ("env-user", "env-pass")


def test_missing_smoke_credentials_fail_the_lane_and_never_skip(monkeypatch, tmp_path):
    """Decision 6 again: without the smoke account the smoke-auth and web-auth checks
    would skip, and the lane would exit green on a misconfigured box."""
    monkeypatch.delenv("CI_SMOKE_USER", raising=False)
    monkeypatch.delenv("CI_SMOKE_PASS", raising=False)
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "absent.env"))
    # Caught explicitly for the reason the write-credentials pin gives: a skip is
    # not a failure, and would report this pin itself as skipped.
    try:
        require_smoke_creds()
    except pytest.skip.Exception:
        pytest.fail("require_smoke_creds skipped; a missing smoke account must fail "
                    "the lane, never skip it")
    except pytest.fail.Exception as failed:
        message = str(failed)
    else:
        pytest.fail("require_smoke_creds returned with no credentials set anywhere")
    for name in ("CI_SMOKE_USER", "CI_SMOKE_PASS", "ci.env"):
        assert name in message, f"the failure does not name {name}: {message}"


def test_smoke_credentials_come_from_the_environment_or_the_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CI_SMOKE_USER", raising=False)
    monkeypatch.delenv("CI_SMOKE_PASS", raising=False)
    env_file = tmp_path / "ci.env"
    env_file.write_text("CI_SMOKE_USER=file-user\nCI_SMOKE_PASS=file-pass\n")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(env_file))
    assert require_smoke_creds() == ("file-user", "file-pass")
    monkeypatch.setenv("CI_SMOKE_USER", "env-user")
    monkeypatch.setenv("CI_SMOKE_PASS", "env-pass")
    assert require_smoke_creds() == ("env-user", "env-pass")


# conftest fixtures that skip when an account is missing: write_creds and
# smoke_creds themselves, and the clients and browser state built on smoke_creds.
SKIPPING_FIXTURES = frozenset({"write_creds", "smoke_creds", "api", "web",
                               "storage_state", "page"})


def test_no_nessie_fixture_or_test_takes_a_skipping_credentials_fixture():
    """The opt-in write lane and the general sweep rely on those fixtures skipping.
    Any Nessie test or fixture that requests one turns a missing account back into
    a green skip, which decision 6 rules out. Plain helpers (_poll, _ask) take
    clients by argument and are not fixtures, so only tests and fixtures count."""
    import ast
    import ci.smoke.test_nessie as nessie
    tree = ast.parse(Path(nessie.__file__).read_text())

    def is_fixture(node) -> bool:
        return any("fixture" in ast.unparse(d) for d in node.decorator_list)

    takers = sorted(
        f"{node.name}({arg.arg})"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name.startswith("test_") or is_fixture(node))
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if arg.arg in SKIPPING_FIXTURES
    )
    assert takers == [], f"these request a fixture that skips: {takers}"


# --------------------------------------------------------------------------- #
# cleanup (spec 3.1, decision 8, amended): keep the chat on a failure AND on a
# pass, so an intermittent failure has a passing chat to be diffed against; keep
# only the newest KEEP_PASSING_CHATS passing chats
# --------------------------------------------------------------------------- #

S1 = "00000000-0000-0000-0000-0000000000a1"          # the chat the lane just ran
SESSIONS = f"{BASE}/nextseek_api/assistant/sessions/"


class _Answer:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code, self.text = status_code, text

    def json(self):
        return json.loads(self.text)


def _row(sid: str, title: str, created_at: str) -> dict:
    """One row of GET assistant/sessions/ (SessionListItem)."""
    return {"session_id": sid, "title": title, "created_at": created_at,
            "updated_at": created_at, "query_count": 4, "preview": "What can you do?"}


class _Api:
    """The write account's client. `listed` is what GET assistant/sessions/ returns;
    every DELETE answers `delete_answer` (or raises), except the ones `delete_fails`
    maps to their own answer."""

    def __init__(self, delete_answer: _Answer | None = None,
                 delete_raises: Exception | None = None, *,
                 patch_answer: _Answer | None = None, patch_raises: Exception | None = None,
                 listed: list | None = None, list_answer: _Answer | None = None,
                 delete_fails: dict | None = None):
        self.calls: list[tuple[str, str]] = []
        self.patched: list[dict] = []
        self._delete_answer = delete_answer or _Answer(204)
        self._delete_raises = delete_raises
        self._patch_answer = patch_answer
        self._patch_raises = patch_raises
        self._listed = listed or []
        self._list_answer = list_answer
        self._delete_fails = delete_fails or {}

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url == SESSIONS:
            if self._list_answer is not None:
                return self._list_answer
            return _Answer(200, json.dumps({"total": len(self._listed),
                                            "sessions": self._listed}))
        return _Answer(200, '{"resolved_as": "session"}')

    def patch(self, url, json=None, **kw):
        self.calls.append(("PATCH", url))
        self.patched.append(json)
        if self._patch_raises is not None:
            raise self._patch_raises
        return self._patch_answer or _Answer(200, "{}")

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url))
        if self._delete_raises is not None:
            raise self._delete_raises
        for sid, answer in self._delete_fails.items():
            if sid in url:
                return answer
        return self._delete_answer

    def deleted(self) -> list[str]:
        return [url.rstrip("/").rsplit("/", 1)[-1] for m, url in self.calls if m == "DELETE"]


def test_finish_chat_keeps_a_passing_lane_s_chat_titled_so_the_next_pass_finds_it(tmp_path):
    """Decision 8 amended: a passing chat is the one an intermittent failure is diffed
    against, so it is kept, named in the CI record, and titled with the marker the
    next passing lane prunes by."""
    from ci.smoke.test_nessie import PASSING_CHAT_TITLE
    api = _Api()
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert error is None
    assert kept == {"session_id": S1,
                    "debug_url": f"{BASE}/nextseek_api/nessie/sessions/{S1}/debug/"}
    assert api.calls[0] == ("PATCH", f"{SESSIONS}{S1}/")
    assert api.patched[0]["title"].startswith(PASSING_CHAT_TITLE)
    assert S1 not in api.deleted(), f"a passing lane deleted its own chat: {api.calls}"
    assert not (tmp_path / "debug.json").exists()


def test_the_lane_keeps_three_passing_chats():
    from ci.smoke.test_nessie import KEEP_PASSING_CHATS
    assert KEEP_PASSING_CHATS == 3


def test_a_passing_lane_deletes_every_passing_chat_but_the_newest(tmp_path):
    """The kept set is bounded: after this pass the write account holds the chat just
    kept and the next-newest KEEP_PASSING_CHATS - 1 passing chats, nothing older.
    Chats without the marker (a failing lane's, anything else) are never touched."""
    from ci.smoke.test_nessie import PASSING_CHAT_TITLE
    marked = f"{PASSING_CHAT_TITLE} 2026-09-1"
    listed = [
        _row(S1, f"{PASSING_CHAT_TITLE} 2026-09-18 12:00 UTC", "2026-09-18T11:55:00Z"),
        _row("p-17", marked + "7", "2026-09-17T10:00:00Z"),
        _row("p-15", marked + "5", "2026-09-15T10:00:00Z"),
        _row("p-16", marked + "6", "2026-09-16T10:00:00Z"),   # the list is by update time
        _row("p-14", marked + "4", "2026-09-14T10:00:00Z"),
        _row("fail", "What can you do?", "2026-09-10T10:00:00Z"),  # a failing lane's
        _row("mine", "My own analysis", "2026-09-01T10:00:00Z"),
    ]
    api = _Api(listed=listed)
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert error is None and kept["session_id"] == S1
    assert sorted(api.deleted()) == ["p-14", "p-15"], api.calls


def test_the_prune_never_deletes_the_chat_it_just_kept(tmp_path):
    """Even when the list spells its id differently and dates it oldest."""
    from ci.smoke.test_nessie import PASSING_CHAT_TITLE
    listed = [_row(S1.replace("-", ""), f"{PASSING_CHAT_TITLE} x", "2020-01-01T00:00:00Z")]
    listed += [_row(f"p-{n}", f"{PASSING_CHAT_TITLE} {n}", f"2026-09-1{n}T00:00:00Z")
               for n in range(5)]
    api = _Api(listed=listed)
    finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert S1.replace("-", "") not in api.deleted() and S1 not in api.deleted()
    assert sorted(api.deleted()) == ["p-0", "p-1", "p-2"], api.calls


@pytest.mark.parametrize("patch_answer, patch_raises, expected", [
    (_Answer(500, "server   error"), None, "answered 500: server error"),
    (None, ConnectionError("refused"), "ConnectionError: refused"),
])
def test_a_passing_chat_that_cannot_be_titled_is_deleted_so_the_kept_set_stays_bounded(
        patch_answer, patch_raises, expected, tmp_path):
    """An unmarked chat is one no later run can find to prune, so it is deleted as
    before, and the report says why it was not kept."""
    api = _Api(patch_answer=patch_answer, patch_raises=patch_raises)
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert kept is None
    assert api.deleted() == [S1], api.calls
    assert error is not None and S1 in error and expected in error, error


@pytest.mark.parametrize("delete_answer, delete_raises, expected", [
    (_Answer(500, "server   error"), None, "DELETE answered 500: server error"),
    (_Answer(404), None, "DELETE answered 404"),
    (None, ConnectionError("refused"), "ConnectionError: refused"),
])
def test_finish_chat_reports_a_chat_it_could_not_delete(delete_answer, delete_raises,
                                                        expected, tmp_path):
    """A failed DELETE leaves a chat behind. It must say so, not pass in silence:
    here the untitled passing chat's own DELETE."""
    api = _Api(delete_answer=delete_answer, delete_raises=delete_raises,
               patch_answer=_Answer(500))
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert kept is None
    assert error is not None, "a chat that was not deleted must be reported"
    assert S1 in error, f"the report does not name the chat: {error}"
    assert expected in error, f"the report does not say why: {error}"


def test_a_prune_that_could_not_delete_is_reported_and_the_chat_still_kept(tmp_path):
    from ci.smoke.test_nessie import PASSING_CHAT_TITLE
    listed = [_row(f"p-{n}", f"{PASSING_CHAT_TITLE} {n}", f"2026-09-1{n}T00:00:00Z")
              for n in range(4)]
    api = _Api(listed=listed, delete_fails={"p-0": _Answer(500, "boom")})
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert kept["session_id"] == S1
    assert sorted(api.deleted()) == ["p-0", "p-1"], api.calls
    assert error is not None and "p-0" in error and "DELETE answered 500: boom" in error, error
    assert "p-1" not in error


@pytest.mark.parametrize("list_answer, expected", [
    (_Answer(500, "down"), "answered 500"),
    (_Answer(200, '{"total": 0}'), "no sessions list"),
])
def test_a_sessions_list_the_prune_cannot_read_is_reported(list_answer, expected, tmp_path):
    api = _Api(list_answer=list_answer)
    kept, error = finish_chat(api, BASE, S1, failed=False, evidence_dir=tmp_path)
    assert kept["session_id"] == S1
    assert api.deleted() == []
    assert error is not None and expected in error, error


def test_finish_chat_keeps_a_failing_lane_s_chat_and_its_debug_answer(tmp_path):
    api = _Api()
    kept, error = finish_chat(api, BASE, "s1", failed=True, evidence_dir=tmp_path)
    assert error is None
    assert kept == {"session_id": "s1",
                    "debug_url": f"{BASE}/nextseek_api/nessie/sessions/s1/debug/"}
    assert [method for method, _ in api.calls] == ["GET"], (
        f"a failing lane must keep its chat, untitled and unpruned: {api.calls}")
    assert (tmp_path / "debug.json").read_text() == '{"resolved_as": "session"}'


def test_finish_chat_with_no_chat_does_nothing(tmp_path):
    api = _Api()
    assert finish_chat(api, BASE, None, failed=True, evidence_dir=tmp_path) == (None, None)
    assert api.calls == []


def test_summary_payload_carries_the_cleanup_error():
    out = summary_payload([], ChatBudget(), None, None, cleanup_error="the chat s was not deleted")
    assert out["cleanup_error"] == "the chat s was not deleted"
    assert summary_payload([], ChatBudget(), None, None)["cleanup_error"] is None


# The lane's own chat_run fixture, run in a pytester session with a fake page and
# a fake API. Every other fixture it takes is the real one.
_LANE_CONFTEST = '''
def pytest_addoption(parser):
    parser.addoption("--nessie-no-turns", action="store_true")
'''

_LANE_MODULE = '''
import json
from types import SimpleNamespace

import pytest

from ci.smoke.test_nessie import chat_run, nessie_budget, nessie_failures_before

CALLS = {calls!r}


class Answer:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text

    def json(self):
        return json.loads(self.text)


class Api:
    def get(self, url, **kw):
        self._log("GET", url)
        return Answer(200, '{{"total": 0, "sessions": []}}')

    def patch(self, url, **kw):
        self._log("PATCH", url)
        return Answer(200, "{{}}")

    def delete(self, url, **kw):
        self._log("DELETE", url)
        return Answer(204)

    def _log(self, method, url):
        with open(CALLS, "a") as f:
            f.write(json.dumps([method, url]) + "\\n")


@pytest.fixture(scope="module")
def nessie_admin_api():
    return Api()


@pytest.fixture(scope="module")
def base_url():
    return "http://stack"


@pytest.fixture(scope="module")
def nessie_evidence_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("evidence")


@pytest.fixture(scope="module")
def nessie_page():
    button = SimpleNamespace(click=lambda: None)
    return SimpleNamespace(get_by_test_id=lambda test_id: button)


def test_a_stage_1_check():
    assert {stage_1_passes}


def test_a_turn_check(chat_run):
    assert [r.status for r in chat_run] == ["completed"] * len(chat_run)
'''


def _fake_ask(page, q, rec, index, **_):
    rec.task_id, rec.session_id, rec.status = f"t{index}", "s1", "completed"


def _run_lane(pytester, monkeypatch, tmp_path, *, stage_1_passes: bool):
    """Run a stage 1 test and a turn test through the real chat_run; return the
    pytester result, the summary chat_run wrote and the HTTP methods it sent."""
    import ci.smoke.test_nessie as lane
    monkeypatch.setattr(lane, "_ask", _fake_ask)
    summary, calls = tmp_path / "summary.json", tmp_path / "calls.ndjson"
    monkeypatch.setenv("CI_NESSIE_SUMMARY", str(summary))
    pytester.makeconftest(_LANE_CONFTEST)
    pytester.makepyfile(test_lane=_LANE_MODULE.format(
        calls=str(calls), stage_1_passes=stage_1_passes))
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    methods = ([json.loads(line)[0] for line in calls.read_text().splitlines()]
               if calls.exists() else [])
    return result, json.loads(summary.read_text()), methods


def test_a_stage_1_failure_keeps_the_chat_even_when_every_turn_test_passes(
        pytester, monkeypatch, tmp_path):
    """Spec 3.1 and decision 8: a red lane keeps its chat as it is, with its
    evidence, and never titles or prunes it as a passing chat. A stage 1 failure
    runs before chat_run is first requested, so a failure count taken in
    chat_run's own setup would miss it and treat the chat as a passing one."""
    result, summary, methods = _run_lane(pytester, monkeypatch, tmp_path,
                                         stage_1_passes=False)
    result.assert_outcomes(passed=1, failed=1)
    assert summary["kept_session"] is not None, f"the chat was not kept: {summary}"
    assert summary["kept_session"]["session_id"] == "s1"
    assert summary["evidence_dir"], f"a red lane names no evidence folder: {summary}"
    assert "DELETE" not in methods, f"a red lane deleted its chat: {methods}"
    assert "PATCH" not in methods, f"a red lane titled its chat as a passing one: {methods}"


def test_a_green_lane_keeps_its_chat_names_it_and_reports_no_cleanup_error(
        pytester, monkeypatch, tmp_path):
    """Decision 8 amended: the green lane's chat is the passing one a later
    intermittent failure is diffed against, so it is kept and the record names it."""
    result, summary, methods = _run_lane(pytester, monkeypatch, tmp_path,
                                         stage_1_passes=True)
    result.assert_outcomes(passed=2)
    assert summary["kept_session"] is not None, f"a green lane dropped its chat: {summary}"
    assert summary["kept_session"]["session_id"] == "s1"
    assert summary["evidence_dir"] is None
    assert summary["cleanup_error"] is None
    assert methods == ["PATCH", "GET"], f"expected the title, then the list: {methods}"


def test_offered_spreadsheets_are_the_tables_and_xlsx_files_a_turn_offered():
    artifacts = [
        {"artifact_type": "file", "key": "api_result", "file_format": "json"},
        {"artifact_type": "table", "key": "samples", "label": "Samples"},
        {"artifact_type": "file", "key": "geo_seq_workbooks", "file_format": "xlsx"},
        {"artifact_type": "table"},                 # no key: nothing to download
        "not-a-dict",
    ]
    assert offered_spreadsheets(artifacts) == ["samples", "geo_seq_workbooks"]
    assert offered_spreadsheets(None) == []
    assert offered_spreadsheets([{"artifact_type": "file", "key": "api_result",
                                  "file_format": "json"}]) == [], (
        "a turn that offers only its JSON result must be asked for no spreadsheet")


def test_the_cc_cost_cap_is_a_rate_per_minute_with_a_one_minute_floor():
    """The CC turn's cost bound scales with how long the turn ran: $0.50 a
    minute, never below one minute's worth, so a longer turn is not failed for
    costing a little over a flat $0.50."""
    from ci.smoke.test_nessie import CC_COST_PER_MINUTE_USD, cc_turn_cap_usd
    assert CC_COST_PER_MINUTE_USD == 0.50
    assert cc_turn_cap_usd(None) == 0.50
    assert cc_turn_cap_usd(30.0) == 0.50
    assert cc_turn_cap_usd(60.0) == 0.50
    assert cc_turn_cap_usd(120.0) == 1.00
    assert 0.5400435 <= cc_turn_cap_usd(119.7)   # run 6's turn now passes
    assert not 0.51 <= cc_turn_cap_usd(30.0)     # a 30 s turn over $0.50 still fails
