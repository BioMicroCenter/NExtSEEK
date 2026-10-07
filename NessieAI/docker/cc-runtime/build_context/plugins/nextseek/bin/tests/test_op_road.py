"""The direct road (approach 1, piece 2): the 11 op tools POST NExtSEEK's assistant endpoints with the turn pass,
download their own files into /data/scratch/nextseek-artifacts/, and exit with the op's error code. NExtSEEK is an
httpx.MockTransport; nothing listens on a socket."""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _assistant_client as ac  # noqa: E402
import _nextseek_runner as runner  # noqa: E402
import _op_errors  # noqa: E402
import _op_road  # noqa: E402
import _sidecar_client as sc  # noqa: E402
import _turn_pass as tp  # noqa: E402
from _turn_deadline import TURN_DEADLINE_ENV  # noqa: E402

NOW = 1_800_000_000.0
CHAT = "11111111-1111-4111-8111-111111111111"
PREFIX = "/nextseek_api/assistant"


class _NExtSEEK:
    """Records every request; answers from a table keyed by path (a Response, or a function of the request)."""

    def __init__(self, answers):
        self.answers = answers
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers[request.url.path]
        return answer(request) if callable(answer) else answer


def _ok(op, result, download=None):
    body = {"op": op, "result": result}
    if download:
        body["download"] = download
    return httpx.Response(200, json=body)


def _artifact(content, filename="summary.xlsx"):
    return httpx.Response(200, content=content, headers={"content-disposition": f'attachment; filename="{filename}"'})


def _stall(request):
    raise httpx.ReadTimeout("no answer", request=request)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key, value in {"NEXTSEEK_URL": "http://nextseek_nginx", "NEXTSEEK_TURN_PASS": "pass-1",
                       "NEXTSEEK_USERNAME": "u1", "NEXTSEEK_CHAT_SESSION_ID": CHAT,
                       "NEXTSEEK_SCRATCH_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    for key in ("NEXTSEEK_CC_OPS_ROAD", "NEXTSEEK_DRY_RUN", TURN_DEADLINE_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(_op_road, "_wallclock", lambda: NOW)
    return tmp_path


def _serve(monkeypatch, server):
    def make():
        return ac.AssistantClient(base_url="http://nextseek_nginx", assistant_prefix="nextseek_api/assistant",
                                  auth=tp.TurnPassAuth("pass-1"), transport=httpx.MockTransport(server))
    monkeypatch.setattr(runner, "_make_client", make)
    return server


def _stderr_error(capsys):
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]


READ_OPS = {
    "entity": ("_dispatch_entity", SimpleNamespace(query="mice"), {"query": "mice"}),
    "parse": ("_dispatch_parse", SimpleNamespace(query="mice"), {"query": "mice"}),
    "graph": ("_dispatch_graph", SimpleNamespace(query="mice"), {"query": "mice"}),
    "graph-schema": ("_dispatch_graph_schema", SimpleNamespace(types="TIS", query=""), {"types": "TIS"}),
    "aggregate": ("_dispatch_aggregate", SimpleNamespace(query="how many", parts=""), {"query": "how many"}),
    "api-read": ("_dispatch_api_read", SimpleNamespace(parser_plan="{}", confirmed_write=False), {"parser_plan": "{}"}),
    "run-ls": ("_dispatch_run_ls", SimpleNamespace(run_dir="/runs/r"), {"run_dir": "/runs/r"}),
}


@pytest.mark.parametrize("op", sorted(READ_OPS))
def test_each_op_posts_its_endpoint_with_the_pass(env, monkeypatch, op):
    dispatcher, args, body = READ_OPS[op]
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/{op}/": _ok(op, {"ok": True})}))

    assert getattr(runner, dispatcher)(args) == {"ok": True}

    [request] = server.requests
    assert (request.method, request.url.path) == ("POST", f"{PREFIX}/{op}/")
    assert json.loads(request.content) == body
    assert request.headers["authorization"] == "NextseekTurn pass-1"


def test_only_the_artifact_ops_send_the_chat_session(env, monkeypatch):
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/report/": _ok("report", {"saved_files": {}}),
                                            f"{PREFIX}/graph/": _ok("graph", {})}))
    runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    runner._dispatch_graph(SimpleNamespace(query="mice"))
    report, graph = (json.loads(request.content) for request in server.requests)
    assert report == {"mode": "samples", "project": "p", "session_id": CHAT}
    assert "session_id" not in graph


@pytest.mark.parametrize("op, left, expected", [
    ("graph", None, 100.0),    # no turn deadline in the env: the limit plus 10 s
    ("report", None, 160.0),
    ("graph", 200.0, 100.0),   # plenty of turn left: still the limit plus 10 s
    ("report", 120.0, 75.0),  # the turn ends first: what is left less the 45 s headroom
    ("graph", 20.0, 10.0),    # nearly out: today's 10 s floor (plan 04 replaces it with a refusal)
])
def test_the_tool_waits_the_op_limit_plus_ten_seconds_within_the_turn(env, monkeypatch, op, left, expected):
    if left is not None:
        monkeypatch.setenv(TURN_DEADLINE_ENV, str(int(NOW + left)))
    assert _op_road.wait_for(op, NOW) == pytest.approx(expected)


def test_the_wait_reaches_the_request(env, monkeypatch):
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/report/": _ok("report", {"saved_files": {}})}))
    runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert server.requests[0].extensions["timeout"]["read"] == 160.0


def test_the_turn_cut_wait_reaches_the_request(env, monkeypatch):
    monkeypatch.setenv(TURN_DEADLINE_ENV, str(int(NOW + 120)))
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/report/": _ok("report", {"saved_files": {}})}))
    runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert server.requests[0].extensions["timeout"]["read"] == 75.0


def test_an_unknown_reason_never_reaches_stderr(env, monkeypatch, capsys):
    body = {"code": "AGENT_FAILED", "reason": "weird", "message": "m", "errors": []}
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": httpx.Response(502, json=body)}))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))
    assert exc.value.code == 4
    assert "reason" not in _stderr_error(capsys)


@pytest.mark.parametrize("code, status",[("VALIDATION", 422), ("WRITE_BLOCKED", 403), ("AUTH_FAILED", 401),
                                          ("PASS_NOT_ALLOWED", 403), ("BUSY", 429), ("TIME_UP", 408),
                                          ("AGENT_FAILED", 502)])
def test_each_op_error_code_exits_with_its_number(env, monkeypatch, capsys, code, status):
    errors = ([{"field": "query", "type": "missing"}] if code == "VALIDATION"
              else [{"title": code, "detail": "A fixed sentence."}])
    body = {"code": code, "reason": "deadline" if code == "AGENT_FAILED" else None,
            "message": "A fixed sentence.", "errors": errors}
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": httpx.Response(status, json=body)}))

    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))

    assert exc.value.code == _op_errors.EXIT[code]
    error = _stderr_error(capsys)
    assert (error["code"], error["message"]) == (code, "A fixed sentence.")
    assert error.get("reason") == ("deadline" if code == "AGENT_FAILED" else None)
    assert error.get("errors") == ([{"field": "query", "type": "missing"}] if code == "VALIDATION" else None)


def test_the_new_exit_numbers():
    assert {code: _op_errors.EXIT[code] for code in ("BUSY", "TIME_UP", "PASS_NOT_ALLOWED")} == {
        "BUSY": 10, "TIME_UP": 11, "PASS_NOT_ALLOWED": 12}


@pytest.mark.parametrize("status, body, code", [
    (401, {"detail": "Authentication credentials were not provided."}, "AUTH_FAILED"),
    (403, {"detail": "PASS_NOT_ALLOWED: this route is not open to a turn pass"}, "PASS_NOT_ALLOWED"),
    (403, {"detail": "You do not have permission to perform this action."}, "PASS_NOT_ALLOWED"),
    (502, None, "TRANSPORT_ERROR"),
    (500, None, "AGENT_FAILED"),
])
def test_a_reply_without_a_code_is_mapped_by_its_status(env, monkeypatch, capsys, status, body, code):
    reply = (httpx.Response(status, json=body) if body is not None
             else httpx.Response(status, text="<html>error</html>"))
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": reply}))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))
    assert exc.value.code == _op_errors.EXIT[code]
    assert _stderr_error(capsys)["code"] == code


def test_a_403_without_a_code_says_the_user_may_not_use_the_project(env, monkeypatch, capsys):
    # P03-W3-1 item 8 (operator ruling 2026-10-02): the project-membership refusal, exit 12.
    reply = httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": reply}))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))
    assert exc.value.code == 12
    assert _stderr_error(capsys) == {
        "code": "PASS_NOT_ALLOWED",
        "message": "NExtSEEK refused this request (HTTP 403): the user may not use this project or route."}


@pytest.mark.parametrize("left, fragment", [
    (None, "NExtSEEK did not answer within 100 s."),
    (60.0, "This turn was nearly out of time"),
    (100.0, "NExtSEEK did not answer within 55 s, and this turn has no time left for another try"),
])
def test_a_wait_that_runs_out_is_a_transport_error_that_says_why(env, monkeypatch, capsys, left, fragment):
    if left is not None:
        monkeypatch.setenv(TURN_DEADLINE_ENV, str(int(NOW + left)))
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": _stall}))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))
    assert exc.value.code == 7
    assert fragment in _stderr_error(capsys)["message"]


def test_a_reports_files_land_in_scratch_and_the_result_names_them(env, monkeypatch):
    download = {"session_id": CHAT, "bundle_id": 3,
                "artifacts": [{"key": "summary_xlsx", "url": f"{PREFIX}/sessions/{CHAT}/bundles/3/artifacts/summary_xlsx/"}]}
    server = _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/report/": _ok("report", {"summary": {}, "saved_files": {"summary_xlsx": "/app/outputs/x.xlsx"}},
                                 download),
        f"{PREFIX}/sessions/{CHAT}/bundles/3/artifacts/summary_xlsx/": _artifact(b"xlsx-bytes"),
    }))

    out = runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))

    landed = env / "nextseek-artifacts" / "summary.xlsx"
    assert landed.read_bytes() == b"xlsx-bytes"
    assert out["saved_files"]["summary_xlsx"] == str(landed)
    assert server.requests[1].headers["authorization"] == "NextseekTurn pass-1"
    assert [path.name for path in (env / "nextseek-artifacts").iterdir()] == ["summary.xlsx"]  # no .part left


@pytest.mark.parametrize("left, expected", [(None, 30.0), (60.0, 15.0)])
def test_a_download_waits_no_longer_than_the_turn_leaves(env, monkeypatch, left, expected):
    if left is not None:
        monkeypatch.setenv(TURN_DEADLINE_ENV, str(int(NOW + left)))
    download = {"session_id": CHAT, "bundle_id": 3, "artifacts": [{"key": "summary_xlsx", "url": "unused"}]}
    server = _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/report/": _ok("report", {"saved_files": {}}, download),
        f"{PREFIX}/sessions/{CHAT}/bundles/3/artifacts/summary_xlsx/": _artifact(b"xlsx-bytes"),
    }))
    runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert server.requests[1].extensions["timeout"]["read"] == expected


def test_a_submissions_files_are_named_under_staged_files(env, monkeypatch):
    download = {"session_id": CHAT, "bundle_id": 4, "artifacts": [
        {"key": "geo_seq_workbooks", "url": "unused"}, {"key": "all_tables", "url": "unused"}]}
    _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/generate-submission/": _ok("generate-submission", {"report_type": "GEO"}, download),
        f"{PREFIX}/sessions/{CHAT}/bundles/4/artifacts/geo_seq_workbooks/": _artifact(b"geo", "GEO.xlsx"),
        f"{PREFIX}/sessions/{CHAT}/bundles/4/artifacts/all_tables/": _artifact(b"tables", "report_4.xlsx"),
    }))
    out = runner._dispatch_generate_submission(SimpleNamespace(type="GEO", uids="MUS-1", query=None))
    assert out["staged_files"] == {"geo_seq_workbooks": str(env / "nextseek-artifacts" / "GEO.xlsx"),
                                   "all_tables": str(env / "nextseek-artifacts" / "report_4.xlsx")}


def test_a_file_of_the_same_name_is_never_overwritten(env, monkeypatch):
    earlier = env / "nextseek-artifacts" / "summary.xlsx"
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b"earlier op")
    download = {"session_id": CHAT, "bundle_id": 5, "artifacts": [{"key": "summary_xlsx", "url": "unused"}]}
    _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/report/": _ok("report", {"saved_files": {}}, download),
        f"{PREFIX}/sessions/{CHAT}/bundles/5/artifacts/summary_xlsx/": _artifact(b"this op"),
    }))
    out = runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert earlier.read_bytes() == b"earlier op"
    assert out["saved_files"]["summary_xlsx"] == str(env / "nextseek-artifacts" / "5-summary_xlsx-summary.xlsx")


def test_a_name_taken_after_the_check_is_never_overwritten(env, monkeypatch):
    # A second op of the turn takes the free name between the check and the rename.
    free_name = ac._free_name

    def racing_free_name(*args):
        path = free_name(*args)
        path.write_bytes(b"racer")
        return path

    monkeypatch.setattr(ac, "_free_name", racing_free_name)
    download = {"session_id": CHAT, "bundle_id": 5, "artifacts": [{"key": "summary_xlsx", "url": "unused"}]}
    _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/report/": _ok("report", {"saved_files": {}}, download),
        f"{PREFIX}/sessions/{CHAT}/bundles/5/artifacts/summary_xlsx/": _artifact(b"this op"),
    }))
    out = runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    folder = env / "nextseek-artifacts"
    assert (folder / "summary.xlsx").read_bytes() == b"racer"
    assert out["saved_files"]["summary_xlsx"] == str(folder / "5-summary_xlsx-summary.xlsx")
    assert (folder / "5-summary_xlsx-summary.xlsx").read_bytes() == b"this op"
    assert not list(folder.glob("*.part")) and not list(folder.glob(".*.part"))


@pytest.mark.parametrize("download", [
    {"session_id": CHAT, "bundle_id": 3, "artifacts": [{"key": "../../x", "url": "unused"}]},
    {"session_id": "not-a-uuid", "bundle_id": 3, "artifacts": []},
    {"session_id": CHAT, "bundle_id": True, "artifacts": []},
])
def test_an_unusable_download_list_is_a_staging_error_and_fetches_nothing(env, monkeypatch, download):
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/report/": _ok("report", {"saved_files": {}}, download)}))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert exc.value.code == 9
    assert len(server.requests) == 1


def test_a_refused_download_is_a_staging_error(env, monkeypatch, capsys):
    download = {"session_id": CHAT, "bundle_id": 3, "artifacts": [{"key": "summary_xlsx", "url": "unused"}]}
    _serve(monkeypatch, _NExtSEEK({
        f"{PREFIX}/report/": _ok("report", {"saved_files": {}}, download),
        f"{PREFIX}/sessions/{CHAT}/bundles/3/artifacts/summary_xlsx/": httpx.Response(403, json={"detail": "no"}),
    }))
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_report(SimpleNamespace(mode="samples", project="p"))
    assert exc.value.code == 9
    assert "HTTP 403" in _stderr_error(capsys)["message"]
    assert not list((env / "nextseek-artifacts").glob("*.part"))


@pytest.mark.parametrize("header, name", [
    ('attachment; filename="../../etc/x y.xlsx"', "x_y.xlsx"),
    ('attachment; filename=".hidden"', "hidden"),
    ("attachment; filename*=UTF-8''n%C3%A9.xlsx", "summary_xlsx"),
    (None, "summary_xlsx"),
])
def test_the_file_name_is_a_plain_basename(header, name):
    assert ac._artifact_name(header, "summary_xlsx") == name


@pytest.mark.parametrize("value", ["sidecar", "Sidecar ", "SIDECAR"])
def test_the_env_picks_the_sidecar_road(env, monkeypatch, value):
    monkeypatch.setenv("NEXTSEEK_CC_OPS_ROAD", value)
    seen = {}

    def call_op(op, body, *, ns_turn, sidecar_url):
        seen.update(op=op, body=body, ns_turn=ns_turn)
        return {"via": "sidecar"}

    monkeypatch.setattr(sc, "call_op", call_op)
    monkeypatch.setattr(runner, "_make_client", lambda: pytest.fail("the direct road was taken"))
    assert runner._dispatch_report(SimpleNamespace(mode="samples", project="p")) == {"via": "sidecar"}
    assert seen == {"op": "report", "body": {"mode": "samples", "project": "p"}, "ns_turn": ("u1", "pass-1")}


@pytest.mark.parametrize("value", ["side-car", "websocket", ""])
def test_anything_else_is_the_direct_road(env, monkeypatch, value):
    monkeypatch.setenv("NEXTSEEK_CC_OPS_ROAD", value)
    monkeypatch.setattr(sc, "call_op", lambda *a, **k: pytest.fail("the sidecar road was taken"))
    _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/graph/": _ok("graph", {"ok": True})}))
    assert runner._dispatch_graph(SimpleNamespace(query="mice")) == {"ok": True}


def test_the_sidecar_road_keeps_its_own_exit_codes(env, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_CC_OPS_ROAD", "sidecar")

    def call_op(*args, **kwargs):
        raise sc.SidecarCallError("CONFIG_ERROR", "setup failed: KeyError")

    monkeypatch.setattr(sc, "call_op", call_op)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_graph(SimpleNamespace(query="mice"))
    assert exc.value.code == 6


ALL_OPS = {
    **{op: (name, args) for op, (name, args, _body) in READ_OPS.items()},
    "api-write": ("_dispatch_api_write", SimpleNamespace(parser_plan="{}", confirmed_write=True)),
    "report": ("_dispatch_report", SimpleNamespace(mode="samples", project="p")),
    "generate-submission": ("_dispatch_generate_submission", SimpleNamespace(type="GEO", uids="MUS-1", query=None)),
    "build-upload-xlsx": ("_dispatch_build_upload_xlsx", SimpleNamespace(rows="[]", existing_parent_uids=None)),
}


@pytest.mark.parametrize("op", sorted(ALL_OPS))
def test_no_op_body_ever_carries_use_prod(env, monkeypatch, op):
    name, args = ALL_OPS[op]
    server = _serve(monkeypatch, _NExtSEEK({f"{PREFIX}/{op}/": _ok(op, {})}))
    getattr(runner, name)(args)
    assert "use_prod" not in json.loads(server.requests[0].content)
