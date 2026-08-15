"""Hermetic tests for nextseek_api/cc_assistant/scripts/full_ui_e2e.py (Task 12).

No live browser, no live LLM spend, no real MySQL. `run_variant_browser` and
`fetch_chat_session_row` are monkeypatched at the `full_ui_e2e` module level
for every test.

Run exactly as specified for this task (ephemeral pytest-only env, no
project deps, no conftest.py — proves the orchestrator's own import surface
really is limited to the e2e package + mysql helper + stdlib, as required):

    cd /home/taishajo/work/NExtSEEK-merge && \\
      uv run --no-project --with pytest python -m pytest -q --noconftest \\
      nextseek_api/cc_assistant/tests/test_full_ui_e2e.py

That environment has NEITHER pydantic NOR playwright NOR mysql-connector
installed (verified directly: `uv run --no-project --with pytest python -c
"import pydantic"` -> ModuleNotFoundError). full_ui_e2e.py's top-level
imports are `from e2e.catalog import ...`, `from e2e.criteria import
check_pass`, `from e2e.playwright.mysql import fetch_chat_session_row`, and
`from e2e.playwright.runner import run_variant_browser` — the real
chat_nextseek/e2e/catalog.py needs pydantic and chat_nextseek/e2e/playwright/
runner.py needs playwright, neither available here. So this file installs
lightweight fake `e2e.*` modules into sys.modules BEFORE importing
full_ui_e2e (Python's import system checks sys.modules first, so the F4
sys.path.insert() inside full_ui_e2e.py never actually reaches disk here —
harmless no-op in this test, exercised for real by chat_nextseek's own
pytest run of tests/test_e2e_playwright_runner.py, see
test_run_variant_browser_returns_and_persists_session_id there for the F4a
regression proof `mysql.connector` is never imported either, since
`_OrchestratorConfig._connect_db` imports it lazily on first *call*, and no
test here ever lets a real DB connection be attempted (fetch_chat_session_row
is always monkeypatched).

IMPORTANT CAVEAT (per the Task 12 spec): a fake-runner test alone is not
sufficient proof the real gate works end-to-end. These tests prove the
orchestrator's own plumbing (forbidden-phrase/cost/session/hash gating,
--validate-only recompute-from-artifacts) is correct against a runner whose
`turn_results` shape matches the real one exactly — see
`test_fake_runner_turn_results_shape_has_no_last_reply_key`. The real proof
that `run_variant_browser` itself, Playwright traces, and DB-bound session
rows behave this way comes from Task 17's live run + replay against this
same --validate-only path.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


# ── Fake e2e.* modules (installed once, before importing full_ui_e2e) ────


def _install_fake_e2e_modules() -> None:
    if "e2e" in sys.modules:
        return

    e2e_pkg = types.ModuleType("e2e")
    e2e_pkg.__path__ = []  # mark as a package
    sys.modules["e2e"] = e2e_pkg

    # -- e2e.catalog: plain duck-typed stand-ins for the real pydantic models
    # (chat_nextseek/e2e/catalog.py:11-29). Field names/defaults mirror the
    # real PassCriterion(field, op, value=None) / Turn(label, query,
    # pass_criteria=[]) / Variant(family, id, name, turns, tags=[],
    # requires_env=[]) exactly.
    catalog_mod = types.ModuleType("e2e.catalog")

    class PassCriterion:
        def __init__(self, field, op, value=None):
            self.field = field
            self.op = op
            self.value = value

    class Turn:
        def __init__(self, label, query, pass_criteria=None):
            self.label = label
            self.query = query
            self.pass_criteria = pass_criteria or []

    class Variant:
        def __init__(self, family, id, name, turns, tags=None, requires_env=None):
            self.family = family
            self.id = id
            self.name = name
            self.turns = turns
            self.tags = tags or []
            self.requires_env = requires_env or []

    catalog_mod.PassCriterion = PassCriterion
    catalog_mod.Turn = Turn
    catalog_mod.Variant = Variant
    sys.modules["e2e.catalog"] = catalog_mod

    # -- e2e.criteria: minimal check_pass covering only what these tests'
    # ui_text.assistant_reply / mentions|contains|eq|nonempty criteria need.
    # Real semantics (chat_nextseek/e2e/criteria.py) are exercised by
    # chat_nextseek's own pytest suite (tests/test_e2e_criteria*.py); this
    # stand-in only has to prove full_ui_e2e's *plumbing* into check_pass
    # (debug/criteria/browser_ctx/console_text/mysql_chat_log/run_root
    # wiring) is correct, which does not require the full DSL.
    criteria_mod = types.ModuleType("e2e.criteria")

    def check_pass(debug, criteria, *, session=None, last_reply=None, run_root=None,
                    browser_ctx=None, console_text=None, mysql_chat_log=None):
        results = []
        chat_page = (browser_ctx or {}).get("chat_page")
        for c in criteria:
            field, op, expected = c.field, c.op, c.value
            actual = chat_page.latest_assistant_reply() if (field == "ui_text.assistant_reply" and chat_page) else None
            if op == "mentions":
                passed = isinstance(actual, str) and isinstance(expected, str) and expected.lower() in actual.lower()
            elif op == "contains":
                passed = isinstance(actual, str) and bool(expected) and expected in actual
            elif op == "nonempty":
                passed = bool(actual)
            elif op == "eq":
                passed = actual == expected
            else:
                passed = False
            results.append({"field": field, "op": op, "value": expected, "passed": passed})
        all_passed = all(r["passed"] for r in results) if results else True
        return all_passed, results

    criteria_mod.check_pass = check_pass
    sys.modules["e2e.criteria"] = criteria_mod

    playwright_pkg = types.ModuleType("e2e.playwright")
    playwright_pkg.__path__ = []
    sys.modules["e2e.playwright"] = playwright_pkg

    mysql_mod = types.ModuleType("e2e.playwright.mysql")

    def fetch_chat_session_row(config, session_id, *, env="dev"):  # pragma: no cover — always monkeypatched in tests
        return []

    mysql_mod.fetch_chat_session_row = fetch_chat_session_row
    sys.modules["e2e.playwright.mysql"] = mysql_mod

    runner_mod = types.ModuleType("e2e.playwright.runner")

    def run_variant_browser(variant, config, out_dir, **kwargs):  # pragma: no cover — always monkeypatched in tests
        raise NotImplementedError("tests must monkeypatch full_ui_e2e.run_variant_browser")

    runner_mod.run_variant_browser = run_variant_browser
    runner_mod.__name__ = "e2e.playwright.runner"
    sys.modules["e2e.playwright.runner"] = runner_mod


_install_fake_e2e_modules()

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "nextseek_api/cc_assistant/scripts/full_ui_e2e.py"
_spec = importlib.util.spec_from_file_location(
    "nextseek_api.cc_assistant.scripts.full_ui_e2e", _SCRIPT_PATH
)
full_ui_e2e = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = full_ui_e2e
_spec.loader.exec_module(full_ui_e2e)


# ── Fixtures / helpers ────────────────────────────────────────────────────


def _approval(*, base_url="http://localhost:18000", require_non_8000=True, max_total_usd=15.0,
              forbidden_phrases=None, questions=None, instance_identity=None):
    return {
        "base_url": base_url,
        "require_non_8000": require_non_8000,
        "max_total_usd": max_total_usd,
        "forbidden_phrases": forbidden_phrases if forbidden_phrases is not None else [
            "backend is unreachable", "i cannot access",
        ],
        "questions": questions if questions is not None else [
            {"id": "q1", "family": "search_advanced", "name": "basic",
             "turns": [{"label": "main", "query": "Find me mice treated with NDMA",
                        "pass_criteria": [{"field": "ui_text.assistant_reply", "op": "mentions", "value": "NDMA"}]}]},
        ],
        "instance_identity": instance_identity,
    }


def _fake_run_variant_browser_factory(*, reply_text="NDMA mice found here", session_id="sess-abc123",
                                       write_session_file=True, return_session_id=True, status="passed",
                                       route="cc", frame_cost=0.05):
    """Stand-in for chat_nextseek/e2e/playwright/runner.py::run_variant_browser.
    Writes exactly the artifacts the real (poll-transport) runner writes
    (turns/<label>/query.txt, complete.json, ui_text.json; trace.zip;
    session_id.txt) and returns turn_results entries shaped EXACTLY like the
    real ones: {label, passed, elapsed_s, route, criteria_results} — NO
    "last_reply" key (see test_fake_runner_turn_results_shape_has_no_last_reply_key).

    ``route`` shapes the persisted complete.json (query_complete.data) so
    --validate-only's route recompute agrees with the recorded route: the CC
    route carries total_cost_usd/cc_session_id (cost gated on the DB cc_traces
    read), the NS route carries a debug dict and no cost. Defaults to "cc" so
    the pre-existing cost-gating tests keep exercising the cost path."""

    def _complete_data():
        if route == "cc":
            d = {"reply": reply_text, "cc_session_id": f"cc-{session_id}"}
            if frame_cost is not None:  # None -> simulate a CC turn with no cost signal at all
                d["total_cost_usd"] = frame_cost
            return d
        return {"reply": reply_text, "debug": {}}

    def _fake(variant, config, out_dir, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        turn_results = []
        for turn in variant.turns:
            turn_dir = out_dir / "turns" / turn.label
            turn_dir.mkdir(parents=True, exist_ok=True)
            (turn_dir / "query.txt").write_text(turn.query, encoding="utf-8")
            (turn_dir / "complete.json").write_text(json.dumps(_complete_data()), encoding="utf-8")
            (turn_dir / "ui_text.json").write_text(
                json.dumps({"latest_assistant_reply": reply_text}), encoding="utf-8")
            turn_results.append({"label": turn.label, "passed": True, "elapsed_s": 0.1,
                                 "route": route, "criteria_results": []})
        (out_dir / "trace.zip").write_bytes(b"PK\x03\x04fake-trace-bytes")
        (out_dir / "downloaded_keys.json").write_text(json.dumps([]), encoding="utf-8")
        if write_session_file and session_id:
            (out_dir / "session_id.txt").write_text(session_id, encoding="utf-8")
        result = {"id": variant.id, "family": variant.family, "status": status,
                  "elapsed_s": 0.1, "failed_criteria": [], "turn_results": turn_results,
                  "route": route, "cc_cost_usd": (frame_cost if route == "cc" else None)}
        if return_session_id:
            result["session_id"] = session_id
        return result

    return _fake


def _fake_fetch_chat_session_row_factory(*, cost_usd=0.05, present=True):
    def _fake(config, session_id, *, env="dev"):
        if not present:
            return []
        return [{"user_query": "q", "assistant_reply": "a", "mode": "cc",
                 "cc_traces": [{"cost_usd": cost_usd}]}]
    return _fake


def _run_main(monkeypatch, tmp_path, approval, *, fake_runner=None, fake_fetch=None, validate_only=False):
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    run_dir = tmp_path / "run"
    if fake_runner is not None:
        monkeypatch.setattr(full_ui_e2e, "run_variant_browser", fake_runner)
    if fake_fetch is not None:
        monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fake_fetch)
    argv = ["full_ui_e2e.py", "--approval", str(approval_path), "--run-dir", str(run_dir)]
    if validate_only:
        argv.append("--validate-only")
    monkeypatch.setattr(sys, "argv", argv)
    exit_code = full_ui_e2e.main()
    return exit_code, run_dir


def _happy_run(monkeypatch, tmp_path, *, reply_text="NDMA mice found here", cost_usd=0.05,
               session_id="sess-abc123", instance_identity=None):
    approval = _approval(instance_identity=instance_identity)
    # CC cost now comes from the runner's result-frame total_cost_usd (frame_cost),
    # not the DB. fake_fetch is kept only for recompute's mysql_log call (trio/
    # mysql_chat_log criteria), which the happy questions don't exercise.
    fake_runner = _fake_run_variant_browser_factory(
        reply_text=reply_text, session_id=session_id, frame_cost=cost_usd)
    fake_fetch = _fake_fetch_chat_session_row_factory(cost_usd=cost_usd)
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner, fake_fetch=fake_fetch)
    assert exit_code == 0, "happy-path fixture must itself pass before it's useful for mutation-detection tests"
    return approval, run_dir, fake_fetch


def _validate(monkeypatch, tmp_path, approval, fake_fetch):
    return _run_main(monkeypatch, tmp_path, approval, fake_fetch=fake_fetch, validate_only=True)


def _resync_manifest_and_summary(run_dir: Path, qid: str) -> None:
    """Simulate a sophisticated tamperer: after editing a tracked file inside
    q_dir, also regenerate manifest.json and update summary.json's
    manifest_sha256 so the local file-integrity layer alone can no longer
    catch the edit — isolates whichever deeper semantic check (session id /
    cost / identity cross-check against DB or against the frozen approval)
    is supposed to catch it instead."""
    q_dir = run_dir / qid
    manifest = full_ui_e2e._artifact_manifest(q_dir)
    manifest_path = q_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    new_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    for r in summary["results"]:
        if r["id"] == qid:
            r["manifest_sha256"] = new_sha
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


# ── Fixture self-check ─────────────────────────────────────────────────────


def test_fake_runner_turn_results_shape_has_no_last_reply_key(tmp_path):
    """Guards the hermetic fixture itself. run_variant_browser's real
    turn_results entries never carry reply text (see the F3 note in
    full_ui_e2e's module docstring) — if this fixture ever grew a
    'last_reply' key it would silently stop exercising the forbidden-phrase
    gate's real code path (_turn_reply_from_artifacts), masking a dead gate."""
    variant = full_ui_e2e.Variant(family="f", id="q1", name="n", turns=[
        full_ui_e2e.Turn(label="main", query="hello", pass_criteria=[]),
    ])
    fake = _fake_run_variant_browser_factory()
    result = fake(variant, object(), tmp_path / "out")
    assert result["turn_results"], "fixture produced no turns"
    for tr in result["turn_results"]:
        assert set(tr.keys()) == {"label", "passed", "elapsed_s", "route", "criteria_results"}
        assert "last_reply" not in tr


# ── reply-text / session-id / cost pure-helper unit tests ──────────────────
# (The old _session_cost_usd cc_traces-summing helper was removed 2026-07-13 —
# CC cost is now the exact result-frame total_cost_usd; see the frame-cost tests
# under main()/validate below and _cc_frame_cost_from_artifacts.)


def test_cc_frame_cost_sums_total_cost_usd_across_turns(tmp_path):
    """_cc_frame_cost_from_artifacts sums the per-turn complete.json
    total_cost_usd (the exact result-frame value), and returns None when no turn
    carries one."""
    q_dir = tmp_path / "q1"
    for label, tc in (("t0", 0.10), ("t1", 0.05)):
        d = q_dir / "turns" / label
        d.mkdir(parents=True)
        (d / "complete.json").write_text(json.dumps({"reply": "r", "total_cost_usd": tc}), encoding="utf-8")
    assert full_ui_e2e._cc_frame_cost_from_artifacts(q_dir) == pytest.approx(0.15)


def test_cc_frame_cost_none_when_no_total_cost_usd(tmp_path):
    q_dir = tmp_path / "q1"
    d = q_dir / "turns" / "main"
    d.mkdir(parents=True)
    (d / "complete.json").write_text(json.dumps({"reply": "r", "debug": {}}), encoding="utf-8")
    assert full_ui_e2e._cc_frame_cost_from_artifacts(q_dir) is None


def test_turn_reply_from_artifacts_prefers_ui_text_json(tmp_path):
    turn_dir = tmp_path / "q1" / "turns" / "main"
    turn_dir.mkdir(parents=True)
    (turn_dir / "ui_text.json").write_text(json.dumps({"latest_assistant_reply": "UI reply"}), encoding="utf-8")
    (turn_dir / "complete.json").write_text(json.dumps({"reply": "WS reply"}), encoding="utf-8")
    assert full_ui_e2e._turn_reply_from_artifacts(tmp_path / "q1", {"label": "main"}) == "UI reply"


def test_turn_reply_from_artifacts_falls_back_to_complete_json(tmp_path):
    turn_dir = tmp_path / "q1" / "turns" / "main"
    turn_dir.mkdir(parents=True)
    (turn_dir / "complete.json").write_text(json.dumps({"reply": "WS reply"}), encoding="utf-8")
    assert full_ui_e2e._turn_reply_from_artifacts(tmp_path / "q1", {"label": "main"}) == "WS reply"


def test_turn_reply_from_artifacts_empty_for_errored_turn(tmp_path):
    turn_dir = tmp_path / "q1" / "turns" / "main"
    turn_dir.mkdir(parents=True)
    (turn_dir / "error.txt").write_text("boom", encoding="utf-8")
    assert full_ui_e2e._turn_reply_from_artifacts(tmp_path / "q1", {"label": "main"}) == ""


def test_session_id_from_artifacts_reads_persisted_file(tmp_path):
    q_dir = tmp_path / "q1"
    q_dir.mkdir()
    (q_dir / "session_id.txt").write_text("sess-xyz\n", encoding="utf-8")
    assert full_ui_e2e._session_id_from_artifacts(q_dir) == "sess-xyz"


def test_session_id_from_artifacts_none_when_absent(tmp_path):
    q_dir = tmp_path / "q1"
    q_dir.mkdir()
    assert full_ui_e2e._session_id_from_artifacts(q_dir) is None


# ── main(): gating behavior (Step 3 required scenarios) ────────────────────


def test_forbidden_phrase_in_reply_fails(monkeypatch, tmp_path):
    approval = _approval(forbidden_phrases=["backend is unreachable"])
    fake_runner = _fake_run_variant_browser_factory(reply_text="Sorry, the backend is unreachable right now.")
    fake_fetch = _fake_fetch_chat_session_row_factory(cost_usd=0.05)
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner, fake_fetch=fake_fetch)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["all_passed"] is False
    assert summary["results"][0]["forbidden_hits"] == ["backend is unreachable"]
    assert summary["results"][0]["passed"] is False


def test_port_8000_with_require_non_8000_exits_1(monkeypatch, tmp_path):
    approval = _approval(base_url="http://localhost:8000", require_non_8000=True)

    def _runner(*_a, **_k):
        raise AssertionError("run_variant_browser must never be called once port 8000 is rejected")

    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=_runner)
    assert exit_code == 1
    assert not run_dir.exists()


def test_cc_missing_frame_cost_fails(monkeypatch, tmp_path):
    """A CC turn whose result frame carries no total_cost_usd fails the
    fail-closed cost gate (every CC-routed turn costs a real Bedrock turn)."""
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(frame_cost=None)  # no result-frame cost
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["cost_usd"] is None
    assert summary["results"][0]["passed"] is False


def test_cc_cost_comes_from_result_frame_no_db_read(monkeypatch, tmp_path):
    """The CC cost is the EXACT result-frame total_cost_usd the poll payload
    already carries (== the value later persisted to cc_traces; see
    translate.py:155 / cc_engine.py:792). It must be read from the frame, with
    NO MySQL cc_traces round-trip — matching the step7 harness
    (cost_source=claude_code_result) and the off-box poll_e2e.cc_cost."""
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(frame_cost=0.31)

    def _fetch_must_not_be_called(config, session_id, *, env="dev"):
        raise AssertionError("the CC cost path must not read the DB (cost comes from the result frame)")

    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner,
                                    fake_fetch=_fetch_must_not_be_called)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["route"] == "cc"
    assert summary["results"][0]["cost_usd"] == pytest.approx(0.31)
    assert summary["results"][0]["passed"] is True
    assert exit_code == 0


def test_missing_session_id_fails(monkeypatch, tmp_path):
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(write_session_file=False, return_session_id=False)

    def _fetch_must_not_be_called(config, session_id, *, env="dev"):
        raise AssertionError("main() must not read the DB (cost comes from the result frame)")

    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner,
                                    fake_fetch=_fetch_must_not_be_called)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["session_id"] is None
    assert summary["results"][0]["passed"] is False  # a missing session id fails the question outright


def test_cc_zero_cost_fails_fail_closed(monkeypatch, tmp_path):
    """A CC turn reporting exactly $0 is fail-closed: every CC-routed turn costs
    a real Bedrock turn, so $0 means the true spend was not captured (matches
    step7_per_op_evidence's cost_usd > 0 rule)."""
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(frame_cost=0.0)
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["cost_usd"] == 0.0
    assert summary["results"][0]["passed"] is False


def test_total_cost_over_cap_fails(monkeypatch, tmp_path):
    approval = _approval(max_total_usd=0.01)
    fake_runner = _fake_run_variant_browser_factory(frame_cost=5.00)  # exact result-frame cost
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["passed"] is True  # the per-question gate passed...
    assert summary["all_passed"] is False           # ...but the cap gate still fails the whole run


# ── main(): route awareness (NS persists no per-turn cost) ──────────────────


def test_ns_route_passes_without_any_cost_row(monkeypatch, tmp_path):
    """The deterministic NS pipeline persists no cc_traces (see the NS branch of
    services/cc_assistant.py). An NS-routed question must pass on criteria +
    session + no-forbidden WITHOUT a cost row — the CC-only cost gate must not
    fail it."""
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(route="ns")

    def _fetch_no_traces(config, session_id, *, env="dev"):
        return [{"user_query": "q", "assistant_reply": "a", "cc_traces": []}]  # NS: no cc_traces

    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner,
                                    fake_fetch=_fetch_no_traces)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["route"] == "ns"
    assert summary["results"][0]["cost_usd"] is None      # no cost persisted on NS
    assert summary["results"][0]["passed"] is True        # but the question still passes
    assert exit_code == 0


def test_ns_route_validate_only_roundtrips(monkeypatch, tmp_path):
    """An NS run replays clean through --validate-only: route recomputed from
    complete.json == recorded, and the NS branch requires no cost row."""
    approval = _approval()
    fake_runner = _fake_run_variant_browser_factory(route="ns")
    fake_fetch = _fake_fetch_chat_session_row_factory(present=True, cost_usd=0.05)  # cc_traces ignored on NS

    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner, fake_fetch=fake_fetch)
    assert exit_code == 0
    exit_code2, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code2 == 0


def test_declared_route_mismatch_fails(monkeypatch, tmp_path):
    """A question that declares an expected route but the runner returns the
    other shape is a routing regression -> the question fails."""
    approval = _approval(questions=[
        {"id": "q1", "family": "search_advanced", "name": "basic", "route": "cc",
         "turns": [{"label": "main", "query": "Find me mice treated with NDMA",
                    "pass_criteria": [{"field": "ui_text.assistant_reply", "op": "mentions", "value": "NDMA"}]}]},
    ])
    fake_runner = _fake_run_variant_browser_factory(route="ns")  # declared cc, got ns
    fake_fetch = _fake_fetch_chat_session_row_factory(cost_usd=0.05)
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner, fake_fetch=fake_fetch)
    assert exit_code == 1
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["results"][0]["route"] == "ns"
    assert summary["results"][0]["declared_route"] == "cc"
    assert summary["results"][0]["route_mismatch"] is True
    assert summary["results"][0]["passed"] is False


# ── --validate-only: drifted approval hash ─────────────────────────────────


def test_validate_only_fails_on_drifted_approval_sha(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    mutated = dict(approval)
    mutated["max_total_usd"] = 999.0  # any byte-level change flips the sha256
    exit_code, _ = _validate(monkeypatch, tmp_path, mutated, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_when_criteria_json_mutated(monkeypatch, tmp_path):
    """'criteria JSON mutated' = the approval's frozen pass_criteria changed
    after spend — must be caught by the same approval-hash anchor as any
    other approval-field drift, since criteria live inside approval.json."""
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    mutated = json.loads(json.dumps(approval))  # deep copy
    mutated["questions"][0]["turns"][0]["pass_criteria"] = [
        {"field": "ui_text.assistant_reply", "op": "nonempty", "value": None},
    ]
    exit_code, _ = _validate(monkeypatch, tmp_path, mutated, fake_fetch)
    assert exit_code == 1


# ── --validate-only: happy path (positive control) ─────────────────────────


def test_validate_only_passes_on_unmutated_run(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 0


# ── --validate-only: replay mutation detection ──────────────────────────────


def test_validate_only_fails_on_mutated_answer_text(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    ui_text_path = run_dir / "q1" / "turns" / "main" / "ui_text.json"
    ui_text_path.write_text(json.dumps({"latest_assistant_reply": "totally different answer"}), encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_on_missing_artifact_file(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    (run_dir / "q1" / "trace.zip").unlink()
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_on_mutated_turn_query(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    query_path = run_dir / "q1" / "turns" / "main" / "query.txt"
    query_path.write_text("a completely different query", encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_on_mutated_session_id_even_with_resynced_manifest(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    (run_dir / "q1" / "session_id.txt").write_text("sess-tampered", encoding="utf-8")
    _resync_manifest_and_summary(run_dir, "q1")  # neutralize the file-integrity layer
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1  # must still be caught: recomputed session_id != summary's recorded one


def test_validate_only_fails_on_mutated_cost_ledger(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path, cost_usd=0.05)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["results"][0]["cost_usd"] = 0.00
    summary["total_cost_usd"] = 0.00
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1  # fresh DB read still says 0.05 -> mismatch


def test_validate_only_fails_on_mutated_browser_url(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    identity_path = run_dir / "identity.json"
    identity = json.loads(identity_path.read_text())
    identity["base_url"] = "http://attacker-controlled:9999"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_on_mutated_db_identity(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(monkeypatch, tmp_path)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["db_identity"] = {"env": "dev", "host": "attacker-db", "port": 3306, "user": "root"}
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_on_mutated_instance_identity(monkeypatch, tmp_path):
    approval, run_dir, fake_fetch = _happy_run(
        monkeypatch, tmp_path,
        instance_identity={"compose_project": "nextseek2", "env_sha256": "deadbeef"},
    )
    identity_path = run_dir / "identity.json"
    identity = json.loads(identity_path.read_text())
    identity["instance_identity"] = {"compose_project": "nextseek2", "env_sha256": "TAMPERED"}
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    exit_code, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code == 1


def test_validate_only_fails_when_all_passed_hand_edited_true(monkeypatch, tmp_path):
    """A hand-edit that flips just the top-level all_passed verdict (without
    touching any per-question evidence) must still be caught by comparing
    against the independently recomputed verdict."""
    approval = _approval(forbidden_phrases=["backend is unreachable"])
    fake_runner = _fake_run_variant_browser_factory(reply_text="Sorry, the backend is unreachable right now.")
    fake_fetch = _fake_fetch_chat_session_row_factory(cost_usd=0.05)
    exit_code, run_dir = _run_main(monkeypatch, tmp_path, approval, fake_runner=fake_runner, fake_fetch=fake_fetch)
    assert exit_code == 1  # forbidden phrase really did fail this run

    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["all_passed"] = True
    summary["results"][0]["passed"] = True
    summary["results"][0]["forbidden_hits"] = []
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    exit_code2, _ = _validate(monkeypatch, tmp_path, approval, fake_fetch)
    assert exit_code2 == 1


def test_connect_db_missing_creds_and_import_error(monkeypatch, capsys):
    cfg = full_ui_e2e._OrchestratorConfig("http://localhost:18000")
    cfg.MYSQL_HOST_DEV = None
    assert cfg._connect_db("dev") is None
    out = capsys.readouterr().out
    assert "credentials not configured" in out
    cfg.MYSQL_HOST_DEV = "h"
    cfg.MYSQL_USER = "u"
    cfg.MYSQL_DEV_PASSWORD = "p"
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mysql.connector":
            raise ImportError("no driver")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert cfg._connect_db("dev") is None
    assert "mysql-connector-python not installed" in capsys.readouterr().out
    page = full_ui_e2e._ArtifactChatPage("hello")
    assert page.latest_assistant_reply() == "hello"
    assert page.bubble_count() is None
    assert page.has_artifact("x") is None
    assert page.stepper_status("x") is None
    ident = cfg.db_identity("prod")
    assert ident["env"] == "prod"


def test_recompute_run_dir_fail_closed_paths(tmp_path):
    approval = _approval()
    approval["_sha256"] = "abc"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert full_ui_e2e.recompute_run_dir(run_dir, approval) is False
    (run_dir / "summary.json").write_text("{")
    (run_dir / "identity.json").write_text("{")
    assert full_ui_e2e.recompute_run_dir(run_dir, approval) is False
    (run_dir / "summary.json").write_text(json.dumps({"approval_sha256": "nope", "results": []}))
    (run_dir / "identity.json").write_text(json.dumps({"base_url": "http://localhost:18000"}))
    assert full_ui_e2e.recompute_run_dir(run_dir, approval) is False
    ident = {"base_url": "http://localhost:8000", "db_identity": {}}
    (run_dir / "identity.json").write_text(json.dumps(ident))
    (run_dir / "summary.json").write_text(json.dumps({
        "approval_sha256": "abc",
        "identity_sha256": "nope",
        "results": [],
        "db_identity": {},
    }))
    approval["base_url"] = "http://localhost:8000"
    approval["require_non_8000"] = True
    approval["_sha256"] = "abc"
    assert full_ui_e2e.recompute_run_dir(run_dir, approval) is False


def test_artifact_helpers_and_recompute_mutations(monkeypatch, tmp_path):
    q = tmp_path / "q"
    turn = q / "turns" / "main"
    turn.mkdir(parents=True)
    (turn / "ui_text.json").write_text("{")
    (turn / "complete.json").write_text("{")
    assert full_ui_e2e._turn_reply_from_artifacts(q, {"label": "main"}) == ""
    (turn / "ui_text.json").write_text(json.dumps({"latest_assistant_reply": ""}))
    (turn / "complete.json").write_text(json.dumps({"reply": "from-complete"}))
    assert full_ui_e2e._turn_reply_from_artifacts(q, {"label": "main"}) == "from-complete"
    assert full_ui_e2e._detect_route_from_data("x") == "unknown"
    assert full_ui_e2e._detect_route_from_data({"debug": {}}) == "ns"
    assert full_ui_e2e._detect_route_from_data({"cc_session_id": "s"}) == "cc"
    assert full_ui_e2e._detect_route_from_data({}) == "unknown"
    assert full_ui_e2e._route_from_artifacts(tmp_path / "missing") == "unknown"
    (turn / "complete.json").write_text("{")
    assert full_ui_e2e._route_from_artifacts(q) == "unknown"
    (q / "turns" / "other").mkdir()
    (q / "turns" / "other" / "complete.json").write_text(json.dumps({"total_cost_usd": 0.2}))
    assert full_ui_e2e._cc_frame_cost_from_artifacts(q) == 0.2
    assert full_ui_e2e._cc_frame_cost_from_artifacts(tmp_path / "no-turns") is None
    (q / "downloaded_keys.json").write_text("{")
    assert full_ui_e2e._downloaded_keys_from_artifacts(q) == set()
    (q / "downloaded_keys.json").write_text(json.dumps(["a", 1]))
    assert "a" in full_ui_e2e._downloaded_keys_from_artifacts(q)
    assert full_ui_e2e._artifact_manifest(tmp_path / "no-dir") == {}

    class Conn:
        pass

    class mysql_mod:
        @staticmethod
        def connect(**kw):
            return Conn()

    import sys
    import types
    pkg = types.ModuleType("mysql")
    pkg.connector = mysql_mod
    monkeypatch.setitem(sys.modules, "mysql", pkg)
    monkeypatch.setitem(sys.modules, "mysql.connector", mysql_mod)
    cfg = full_ui_e2e._OrchestratorConfig("http://x")
    cfg.MYSQL_HOST_DEV = "h"
    cfg.MYSQL_USER = "u"
    cfg.MYSQL_DEV_PASSWORD = "p"
    assert cfg._connect_db("dev") is not None

    def _sha(approval, run_dir):
        approval = dict(approval)
        approval["_sha256"] = json.loads((run_dir / "summary.json").read_text())["approval_sha256"]
        return approval

        qid = "q1"
        for name in ["base", "ns"] + [f"m{i}" for i in range(2, 17)]:
            (tmp_path / name).mkdir()

        approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "base")
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["results"] = []
    (run_dir / "summary.json").write_text(json.dumps(summary))
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m2")
    (run_dir / qid / "manifest.json").unlink()
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m3")
    (run_dir / qid / "manifest.json").write_text("{")
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["results"][0]["manifest_sha256"] = hashlib.sha256(b"{").hexdigest()
    (run_dir / "summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m4")
    (run_dir / qid / "trace.zip").write_bytes(b"")
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m5")
    (run_dir / qid / "session_id.txt").unlink()
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m6")
    (run_dir / qid / "session_id.txt").write_text("other")
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m7")
    (run_dir / qid / "turns" / "main" / "query.txt").unlink()
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m8")
    (run_dir / qid / "turns" / "main" / "query.txt").write_text("mutated-query")
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m9")
    (run_dir / qid / "turns" / "main" / "error.txt").write_text("boom")
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m10")
    complete = run_dir / qid / "turns" / "main" / "complete.json"
    complete.unlink()
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m11")
    (run_dir / qid / "turns" / "main" / "complete.json").write_text("{")
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m12")
    payload = json.loads((run_dir / qid / "turns" / "main" / "complete.json").read_text())
    payload.pop("total_cost_usd", None)
    payload.pop("cc_session_id", None)
    (run_dir / qid / "turns" / "main" / "complete.json").write_text(json.dumps(payload))
    _resync_manifest_and_summary(run_dir, qid)
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m13")
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["results"][0]["cost_usd"] = 99
    (run_dir / "summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m14")
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["results"][0]["route"] = "ns"
    (run_dir / "summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m15")
    approval["questions"][0]["route"] = "ns"
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    approval, run_dir, fetch = _happy_run(monkeypatch, tmp_path / "m16")
    approval["max_total_usd"] = 0.0001
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", fetch)
    assert full_ui_e2e.recompute_run_dir(run_dir, _sha(approval, run_dir)) is False

    ns_approval = _approval()
    ns_runner = _fake_run_variant_browser_factory(route="ns", frame_cost=None)
    ns_fetch = _fake_fetch_chat_session_row_factory()
    _, ns_run = _run_main(monkeypatch, tmp_path / "ns", ns_approval, fake_runner=ns_runner, fake_fetch=ns_fetch)
    summary = json.loads((ns_run / "summary.json").read_text())
    summary["results"][0]["cost_usd"] = 1.0
    (ns_run / "summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(full_ui_e2e, "fetch_chat_session_row", ns_fetch)
    assert full_ui_e2e.recompute_run_dir(ns_run, _sha(ns_approval, ns_run)) is False
