"""The harness side of the prompt-variant switch, and the unforced POC arm.

`prompt_variant` ("v2_apoc") rides on every turn beside `force_route`, like `force_parser_mode`, and is
dropped by the server without a word for anyone but a superuser on a process with NEXTSEEK_EVAL_PARSER_FORCE=1.
The arm `auto` forces the NS route and NOT the parser, so the parser picks graph or API itself, while
`run_arms` still writes every question's payload for the scorer. The preflight proves the variant landed by
reading `debug.prompt_variant` off a finished turn.

Every test drives fakes; none reaches a model. The engine is never imported: the variant names are pinned
against the engine's and the request model's source text, as FORCE_NOTE_MARKER is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import cli, http_driver, preflight, runner
from NessieAI.tests.nessie_tests.manifest import load_manifest

REPO = Path(__file__).resolve().parents[4]
ENGINE_SRC = REPO / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek" / "prompt_variants.py"
MODELS_SRC = REPO / "nextseek_api" / "assistant" / "models_api.py"
CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"
PROBES = {preflight.PROBE_QUERY, preflight.PARSER_FORCE_PROBE_QUERY}


def _question(vid):
    return f"How many samples for {vid} (0)?"


def _qid(body):
    return re.search(r"for (\S+) \(", body["query"]).group(1)


def _cases(tmp_path, ids=("q.one", "q.two")):
    variants = [{"family": "engine_compare", "id": vid, "name": vid, "tags": ["engine_compare"],
                 "turns": [{"label": "t0", "query": _question(vid),
                            "pass_criteria": [{"field": "last_reply", "op": "matches_re", "value": "4"}]}]}
                for vid in ids]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"families": {"engine_compare": {"description": "t", "variants": variants}}}),
                    encoding="utf-8")
    return path


class Endpoint:
    """Answers as a server with the flag would: echoes a sent prompt_variant into the debug payload.

    `drop_variant` models a server that drops the field (not a superuser, no flag, or an old image);
    `parser_mode` is what the unforced parser picks.
    """

    def __init__(self, *, drop_variant=False, parser_mode="graph_query", context="catalog",
                 record_field=True):
        self.bodies = []
        self.drop_variant = drop_variant
        self.parser_mode = parser_mode
        self.context = context
        self.record_field = record_field

    def post_query(self, body):
        self.bodies.append(body)
        return {"task_id": f"t{len(self.bodies)}", "session_id": f"s{len(self.bodies)}"}

    def get_progress(self, task_id):
        body = self.bodies[int(task_id[1:]) - 1]
        arm = body.get("force_parser_mode")
        if arm is None:
            mode, notes = self.parser_mode, "the parser's own choice"
        else:
            mode = "graph_query" if arm == "graph" else "new_search"
            notes = f"forced to {arm} {preflight.FORCE_NOTE_MARKER} (parser chose new_search)"
        debug = {"parser_plan": {"mode": mode, "notes": notes}}
        if self.record_field:
            debug["prompt_variant"] = None if self.drop_variant else body.get("prompt_variant")
        if mode == "graph_query":
            debug["graph_context"] = self.context
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "nextseek_query", "source": "forced"}},
            {"event": "query_complete", "data": {"reply": "There are 4.", "debug": debug,
                                                  "files": [], "artifacts": []}},
            {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}},
        ]}

    def question_bodies(self):
        return [b for b in self.bodies if b["query"] not in PROBES]


def _run_arms(tmp_path, ep, **kw):
    kw.setdefault("arms", ["auto"])
    return runner.run_arms(base_url="http://venue", auth_header="Basic x", corpus_path=CORPUS,
                           cases_path=kw.pop("cases", None) or _cases(tmp_path),
                           out_dir=kw.pop("out", None) or tmp_path / "run",
                           post_query=ep.post_query, get_progress=ep.get_progress,
                           sleep=lambda s: None, clock=lambda: 0.0, **kw)


# --------------------------------------------------------------------------- the names


def test_the_harness_variant_names_are_the_engines():
    src = ENGINE_SRC.read_text(encoding="utf-8")
    names = ", ".join(f'"{n}"' for n in runner.PROMPT_VARIANTS)
    if len(runner.PROMPT_VARIANTS) == 1:
        names += ","  # a one-element tuple literal carries a trailing comma
    assert f"VARIANT_NAMES: tuple[str, ...] = ({names})" in src
    assert runner.PROMPT_VARIANTS == ("v2_apoc",), "F1 promoted v2 and v3 to the defaults"


def test_the_harness_variant_names_are_the_request_models():
    src = MODELS_SRC.read_text(encoding="utf-8")
    literal = ", ".join(f'"{n}"' for n in runner.PROMPT_VARIANTS)
    assert f"prompt_variant: Optional[Literal[{literal}]]" in src


def test_the_arms_are_graph_api_and_the_unforced_auto():
    assert runner.ARM_PRESETS == {
        "graph": {"force_route": "ns", "force_parser_mode": "graph"},
        "api": {"force_route": "ns", "force_parser_mode": "api"},
        "auto": {"force_route": "ns", "force_parser_mode": None},
    }


# --------------------------------------------------------------------------- the driver


def test_prompt_variant_is_not_sent_unless_set():
    ep = Endpoint()
    http_driver.drive("q", tier="full", post_query=ep.post_query, get_progress=ep.get_progress,
                      force_route="ns", sleep=lambda s: None, clock=lambda: 0.0)
    assert "prompt_variant" not in ep.bodies[0]


def test_prompt_variant_is_sent_without_a_parser_force():
    ep = Endpoint()
    http_driver.drive("q", tier="full", post_query=ep.post_query, get_progress=ep.get_progress,
                      force_route="ns", prompt_variant="v2_apoc", sleep=lambda s: None, clock=lambda: 0.0)
    assert ep.bodies[0]["prompt_variant"] == "v2_apoc"
    assert "force_parser_mode" not in ep.bodies[0]


# --------------------------------------------------------------------------- run_suite and run_case


def test_run_suite_sends_the_variant_on_every_turn(tmp_path):
    ep = Endpoint()
    runner.run_suite(base_url="http://x", auth_header="Basic x", tier="full", corpus_path=CORPUS,
                     out_dir=tmp_path / "out", post_query=ep.post_query, get_progress=ep.get_progress,
                     cases_path=_cases(tmp_path), force_route="ns", prompt_variant="v2_apoc",
                     sleep=lambda s: None, clock=lambda: 0.0)
    assert ep.bodies and all(b["prompt_variant"] == "v2_apoc" for b in ep.bodies)
    assert all("force_parser_mode" not in b for b in ep.bodies)


@pytest.mark.parametrize("route", [None, "cc"])
def test_a_variant_needs_the_ns_route(route, tmp_path):
    ep = Endpoint()
    with pytest.raises(ValueError, match="force_route='ns'"):
        runner.run_suite(base_url="http://x", auth_header="Basic x", tier="full", corpus_path=CORPUS,
                         out_dir=tmp_path / "out", post_query=ep.post_query, get_progress=ep.get_progress,
                         cases_path=_cases(tmp_path), force_route=route, prompt_variant="v2_apoc")
    assert ep.bodies == []


def test_an_unknown_variant_is_refused_before_any_turn(tmp_path):
    ep = Endpoint()
    with pytest.raises(ValueError, match="rewrite"):
        runner.run_suite(base_url="http://x", auth_header="Basic x", tier="full", corpus_path=CORPUS,
                         out_dir=tmp_path / "out", post_query=ep.post_query, get_progress=ep.get_progress,
                         cases_path=_cases(tmp_path), force_route="ns", prompt_variant="rewrite")
    assert ep.bodies == []


# --------------------------------------------------------------------------- run_arms, unforced


def test_the_auto_arm_runs_unforced_and_writes_every_payload(tmp_path):
    ep = Endpoint(parser_mode="new_search")
    out = tmp_path / "run"
    result = _run_arms(tmp_path, ep, arms=["auto"], prompt_variant="v2_apoc", out=out)

    questions = ep.question_bodies()
    assert [_qid(b) for b in questions] == ["q.one", "q.two"]
    assert all(b["force_route"] == "ns" and "force_parser_mode" not in b for b in questions)
    assert all(b["prompt_variant"] == "v2_apoc" for b in ep.bodies if b["query"] != preflight.PROBE_QUERY), \
        "the parser probe carries it too"
    assert "prompt_variant" not in next(b for b in ep.bodies if b["query"] == preflight.PROBE_QUERY), \
        "the route probe is an out-of-scope question: no variant"
    for vid in ("q.one", "q.two"):
        doc = json.loads((out / "auto" / "payloads" / vid / "t0.json").read_text(encoding="utf-8"))
        assert doc["prompt_variant"] == "v2_apoc" and doc["force_parser_mode"] is None
        debug = doc["query_complete"]["debug"]
        assert debug["prompt_variant"] == "v2_apoc"                  # what the server ran
        assert debug["parser_plan"]["mode"] == "new_search"     # the route the parser chose
    assert result["run_meta"]["prompt_variant"] == "v2_apoc"
    assert json.loads((out / runner.ARMS_FILE).read_text())["run_meta"]["prompt_variant"] == "v2_apoc"
    assert [e.id for e in load_manifest(out / "auto" / "manifest.json").entries] == ["q.one", "q.two"]


def test_the_default_prompts_run_records_no_variant(tmp_path):
    ep = Endpoint()
    result = _run_arms(tmp_path, ep, arms=["auto"])
    assert all("prompt_variant" not in b for b in ep.bodies)
    assert result["run_meta"]["prompt_variant"] is None


def test_the_forced_arms_carry_the_variant_as_well(tmp_path):
    ep = Endpoint()
    _run_arms(tmp_path, ep, arms=["graph", "api"], prompt_variant="v2_apoc")
    assert {b.get("force_parser_mode") for b in ep.question_bodies()} == {"graph", "api"}
    assert all(b["prompt_variant"] == "v2_apoc" for b in ep.bodies if b["query"] != preflight.PROBE_QUERY)


def test_a_resume_refuses_a_changed_variant_and_sends_nothing(tmp_path):
    """Since F1 there is one legal variant, so the change under test is on/off rather than
    between two names. The refusal is the same: a resume may not switch prompt sets."""
    out = tmp_path / "run"
    cases = _cases(tmp_path)
    _run_arms(tmp_path, Endpoint(), prompt_variant="v2_apoc", out=out, cases=cases, max_turns=0)
    ep = Endpoint()
    with pytest.raises(runner.ArmsRunRefused, match="prompt variant"):
        _run_arms(tmp_path, ep, out=out, cases=cases, resume=True)
    assert ep.bodies == []


def test_a_resume_of_a_run_that_predates_the_field_is_the_default_prompts(tmp_path):
    out = tmp_path / "run"
    cases = _cases(tmp_path)
    _run_arms(tmp_path, Endpoint(), out=out, cases=cases, max_turns=0)
    doc = json.loads((out / runner.ARMS_FILE).read_text(encoding="utf-8"))
    del doc["run_meta"]["prompt_variant"]
    (out / runner.ARMS_FILE).write_text(json.dumps(doc), encoding="utf-8")

    _run_arms(tmp_path, Endpoint(), out=out, cases=cases, resume=True)  # accepted
    with pytest.raises(runner.ArmsRunRefused, match="prompt variant"):
        _run_arms(tmp_path, Endpoint(), prompt_variant="v2_apoc", out=out, cases=cases, resume=True)


def test_run_arms_refuses_an_unknown_variant_before_any_turn(tmp_path):
    ep = Endpoint()
    with pytest.raises(ValueError, match="v9"):
        _run_arms(tmp_path, ep, prompt_variant="v9")
    assert ep.bodies == []


# --------------------------------------------------------------------------- the preflight


def _preflight(ep, arms=("auto",), **kw):
    preflight.assert_parser_force_works(ep.post_query, ep.get_progress, list(arms),
                                        sleep=lambda s: None, clock=lambda: 0.0, **kw)


@pytest.mark.parametrize("mode", ["graph_query", "new_search"])
def test_the_auto_probe_passes_whichever_way_the_parser_goes(mode):
    ep = Endpoint(parser_mode=mode)
    _preflight(ep)
    assert len(ep.bodies) == 1 and "force_parser_mode" not in ep.bodies[0]


def test_the_auto_probe_still_requires_the_live_catalog_on_a_graph_turn():
    with pytest.raises(preflight.ParserForceRejected, match="catalog"):
        _preflight(Endpoint(parser_mode="graph_query", context="fallback"))


def test_the_auto_probe_is_inconclusive_without_a_plan():
    ep = Endpoint()
    real = ep.get_progress

    def no_plan(task_id):
        payload = real(task_id)
        payload["progress"][1]["data"]["debug"] = {}
        return payload
    ep.get_progress = no_plan
    with pytest.raises(preflight.ParserForceRejected, match="INCONCLUSIVE"):
        _preflight(ep)


@pytest.mark.parametrize("arms", [("auto",), ("graph", "api")])
def test_a_landed_variant_passes(arms):
    _preflight(Endpoint(), arms=arms, prompt_variant="v2_apoc")


@pytest.mark.parametrize("arms", [("auto",), ("graph", "api")])
def test_a_dropped_variant_refuses_and_names_every_cause(arms):
    with pytest.raises(preflight.PromptVariantRejected) as e:
        _preflight(Endpoint(drop_variant=True), arms=arms, prompt_variant="v2_apoc")
    msg = str(e.value)
    assert "NEXTSEEK_EVAL_PARSER_FORCE=1" in msg and "superuser" in msg
    assert "prompts/variants/v2" in msg, "a variant that failed to load is logged by the server"
    assert "rebuild" in msg


def test_an_image_that_predates_the_field_refuses_naming_the_rebuild():
    with pytest.raises(preflight.PromptVariantRejected, match="rebuild"):
        _preflight(Endpoint(record_field=False), prompt_variant="v2_apoc")


def test_the_variant_rejection_is_a_preflight_refusal():
    assert issubclass(preflight.PromptVariantRejected, preflight.PreflightRefused)


# --------------------------------------------------------------------------- the module CLI


def _capture(monkeypatch):
    captured = {}

    def fake_run_suite(**kw):
        captured.update(kw)
        from NessieAI.tests.nessie_tests.manifest import NessieManifest
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"], entries=[])
    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    return captured


def test_the_cli_sends_no_variant_by_default(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    cli.main(["--base-url", "http://x", "--out", str(tmp_path)])
    assert captured["prompt_variant"] is None


def test_the_cli_variant_reaches_run_suite_unforced(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    assert cli.main(["--base-url", "http://x", "--force-route", "ns", "--prompt-variant", "v2_apoc",
                     "--out", str(tmp_path)]) == 0
    assert captured["prompt_variant"] == "v2_apoc" and captured["force_parser_mode"] is None


@pytest.mark.parametrize("extra", [[], ["--force-route", "cc"]])
def test_the_cli_variant_needs_the_ns_route(extra, monkeypatch, capsys):
    monkeypatch.setattr(cli.runner, "run_suite", lambda **kw: pytest.fail("reached a spending path"))
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--prompt-variant", "v2_apoc", *extra])
    assert e.value.code == 2
    assert "--prompt-variant" in capsys.readouterr().err


def test_the_cli_refuses_the_variant_with_bayesian(monkeypatch, capsys):
    monkeypatch.setattr(cli.runner, "run_suite", lambda **kw: pytest.fail("reached a spending path"))
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--bayesian", "--prompt-variant", "v2_apoc"])
    assert e.value.code == 2
    assert "--prompt-variant" in capsys.readouterr().err


def test_the_cli_variant_takes_only_its_choices():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--base-url", "http://x", "--prompt-variant", "rewrite"])
