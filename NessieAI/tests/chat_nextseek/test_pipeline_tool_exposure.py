import json
from chat_nextseek.pipeline import agent_tools as at


class _Cfg:
    def __init__(self, tower, luria):
        self.TOWER_ENV_COMPLETE = tower
        self.LURIA_ENV_COMPLETE = luria
        self.LURIA_ENV = {"user": "u", "key": "/k", "working_path": "/p", "host": "luria.mit.edu"}


def _names(schemas):
    return [t["name"] for t in schemas]


def test_tower_never_exposed_even_when_env_complete():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=False)))
    assert "submit_to_tower" not in names          # Tower retired
    assert "submit_to_luria" not in names           # luria env absent here
    # `handoff` is always exposed: an open build must never be able to trap the
    # conversation when the user asks about something else.
    assert names == ["select_pipeline", "resolve_samples", "write_samplesheet", "configure_run",
                     "conclude", "handoff"]


def test_exposure_luria_only():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=False, luria=True)))
    assert "submit_to_luria" in names and "submit_to_tower" not in names


def test_exposure_luria_only_when_both_env_complete():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=True)))
    assert "submit_to_luria" in names and "submit_to_tower" not in names


def test_exposure_neither_still_has_core_and_conclude():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=False, luria=False)))
    assert names == ["select_pipeline", "resolve_samples", "write_samplesheet", "configure_run",
                     "conclude", "handoff"]


def test_handoff_is_always_exposed():
    """Every env combination must offer the escape hatch.

    Without it the agent answers off-topic turns itself ("searching the sample
    database isn't something I can do"), which is how an open samplesheet build
    hijacked an unrelated search.
    """
    for tower, luria in ((False, False), (True, False), (False, True), (True, True)):
        assert "handoff" in _names(at.build_pipeline_tool_schemas(_Cfg(tower=tower, luria=luria)))


def test_tool_submit_to_luria_calls_submitter(monkeypatch):
    monkeypatch.setattr(at, "submit_luria",
                        lambda launch, **kw: [{"job_id": "9", "remote_dir": "/d", "log": "/d/nf-9.out", "run_name": "r"}])
    cfg = _Cfg(tower=False, luria=True)
    state = {"messages": [{"role": "user", "content": "go"}, {"role": "user", "content": "yes"}], "launch_built_at_user_msgs": 1, "artifacts": {"launch": "/tmp/launch.yml"}}
    out = json.loads(at.tool_submit_to_luria(cfg, state, {"resources": {"partition": "bcc"}}))
    assert out["ok"] is True
    assert out["luria_runs"][0]["job_id"] == "9"
    assert state["artifacts"]["luria_runs"][0]["job_id"] == "9"


def test_tool_submit_to_luria_guards_missing_artifact():
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), {"artifacts": {}}, {}))
    assert out["ok"] is False


def _capture_luria(monkeypatch):
    captured = {}
    monkeypatch.setattr(at, "submit_luria",
                        lambda launch, **kw: (captured.update(kw),
                                              [{"job_id": "1", "remote_dir": "/d", "log": "/d/o", "run_name": "r"}])[1])
    return captured


def test_tool_submit_to_luria_threads_params_and_forces_gencode_for_gencode_ref(monkeypatch):
    captured = _capture_luria(monkeypatch)
    state = {"messages": [{"role": "user", "content": "go"}, {"role": "user", "content": "yes"}], "launch_built_at_user_msgs": 1, "artifacts": {"launch": "/tmp/launch.yml", "samplesheet": "/tmp/s.csv"},
             "launch_plan": {"params": {"genome": "GRCm39", "aligner": "star_salmon", "gencode": False}}}
    at.tool_submit_to_luria(_Cfg(tower=False, luria=True), state, {})
    assert captured["genome"] == "GRCm39"
    assert captured["launch_params"]["aligner"] == "star_salmon"
    assert captured["launch_params"]["gencode"] is True   # GRCm39 local refs are GENCODE -> forced on


def test_tool_submit_to_luria_leaves_gencode_off_for_ensembl_macaque(monkeypatch):
    captured = _capture_luria(monkeypatch)
    state = {"messages": [{"role": "user", "content": "go"}, {"role": "user", "content": "yes"}], "launch_built_at_user_msgs": 1, "artifacts": {"launch": "/tmp/launch.yml"},
             "launch_plan": {"params": {"genome": "Mfas6.0", "aligner": "star_salmon", "gencode": False}}}
    at.tool_submit_to_luria(_Cfg(tower=False, luria=True), state, {})
    assert captured["launch_params"]["gencode"] is False  # Ensembl macaque -> stays off


def test_existing_static_schema_unchanged():
    # Regression guard: the Tower-era constant, plus the handoff control tool.
    assert {t["name"] for t in at.PIPELINE_TOOL_SCHEMAS} == {
        "select_pipeline", "resolve_samples", "write_samplesheet", "configure_run",
        "submit_to_tower", "conclude", "handoff"}


# --- launch gate: submit only after the user replied since the run was built ---

def _gate_state(n_user_at_build, extra_msgs=()):
    return {"artifacts": {"launch": "/tmp/launch.yml"}, "launch_built_at_user_msgs": n_user_at_build,
            "messages": [{"role": "user", "content": "run it"}, *extra_msgs]}


_REPLY = {"role": "user", "content": "yes, go"}
_TOOL_RESULT = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}
_REFUSAL = {"ok": False, "message": "Not submitted: the user has not confirmed this run since it was built. "
                                    "Show them what will run, ask them to confirm, and submit only after their reply."}


def test_luria_submit_refused_in_the_turn_the_run_was_built(monkeypatch):
    called = []
    monkeypatch.setattr(at, "submit_luria", lambda *a, **k: called.append(1) or [])
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), _gate_state(1), {}))
    assert out == _REFUSAL and not called


def test_luria_submit_ignores_tool_result_messages(monkeypatch):
    monkeypatch.setattr(at, "submit_luria", lambda *a, **k: [{"job_id": "1", "remote_dir": "/d", "log": "/l", "run_name": "r"}])
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), _gate_state(1, [_TOOL_RESULT, _TOOL_RESULT]), {}))
    assert out == _REFUSAL


def test_luria_submit_proceeds_after_a_user_reply(monkeypatch):
    monkeypatch.setattr(at, "submit_luria", lambda *a, **k: [{"job_id": "9", "remote_dir": "/d", "log": "/l", "run_name": "r"}])
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), _gate_state(1, [_TOOL_RESULT, _REPLY]), {}))
    assert out["ok"] is True and out["luria_runs"][0]["job_id"] == "9"


def test_luria_submit_refused_after_a_rebuild_following_the_reply(monkeypatch):
    called = []
    monkeypatch.setattr(at, "submit_luria", lambda *a, **k: called.append(1) or [])
    state = _gate_state(1, [_REPLY])
    state["launch_built_at_user_msgs"] = at._user_msg_count(state)   # what configure_run records on a rebuild
    assert at._user_msg_count(state) == 2
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), state, {}))
    assert out == _REFUSAL and not called


def test_gate_refuses_a_state_without_a_mark_and_survives_a_session_round_trip(monkeypatch):
    # a state saved before the gate existed (no mark), and one restored through JSON, both refuse until a reply
    state = {"artifacts": {"launch": "/tmp/launch.yml"}, "messages": [{"role": "user", "content": "run it"}]}
    assert json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), state, {})) == _REFUSAL
    restored = json.loads(json.dumps(_gate_state(1)))
    assert json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), restored, {})) == _REFUSAL


def test_tower_submit_is_gated_too(monkeypatch):
    monkeypatch.setattr(at, "submit_launch", lambda p, *, tower_env: ["https://tower/run/1"])
    cfg = _Cfg(tower=True, luria=False)
    cfg.TOWER_ENV = {"access_token": "x", "workspace": "w"}
    assert json.loads(at.tool_submit_to_tower(cfg, _gate_state(1))) == _REFUSAL
    assert json.loads(at.tool_submit_to_tower(cfg, _gate_state(1, [_REPLY])))["ok"] is True
