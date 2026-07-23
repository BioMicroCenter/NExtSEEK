"""Hermetic unit tests for the PURE-LOGIC pieces of
``cc_matrix_gate_harness.py`` (Task 15's RUN_REALSTACK-gated capability-gate
harness): argv construction, matrix-row shaping, and the executor env/
run-kwargs builders. No Docker, no network, no DB, no spend -- these are
plain-function unit tests, independent of whether the harness is ever
executed live.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.tests import cc_matrix_gate_harness as gate
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    SHARED_CRED_KEYS,
    matrix_executor_name,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import BIN_OPS


# ==========================================================================
# build_op_argv
# ==========================================================================

@pytest.mark.parametrize("op", ["nextseek-entity-extract", "nextseek-parse", "nextseek-graph", "nextseek-plan"])
def test_query_only_ops_argv(op):
    assert gate.build_op_argv(op, query="find rna-seq samples") == [op, "--query", "find rna-seq samples"]


@pytest.mark.parametrize("op", ["nextseek-entity-extract", "nextseek-parse", "nextseek-graph", "nextseek-plan"])
def test_query_only_ops_require_query(op):
    with pytest.raises(ValueError):
        gate.build_op_argv(op)


def test_nextseek_query_always_passes_json_flag():
    """--json is REQUIRED for the excerpt to be parseable JSON matching the
    op's allowlist -- the bin's default (no --json) form prints only the
    extracted .reply string."""
    argv = gate.build_op_argv("nextseek-query", query="what projects exist?")
    assert argv == ["nextseek-query", "--query", "what projects exist?", "--json"]


def test_api_read_argv():
    argv = gate.build_op_argv("nextseek-api-read", parser_plan='{"endpoint": "/samples/"}')
    assert argv == ["nextseek-api-read", "--parser-plan", '{"endpoint": "/samples/"}']


def test_api_read_requires_parser_plan():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-api-read")


def test_api_write_unconfirmed_leg_omits_confirmed_write_flag():
    argv = gate.build_op_argv("nextseek-api-write", parser_plan="{}")
    assert "--confirmed-write" not in argv


def test_api_write_confirmed_leg_includes_flag():
    argv = gate.build_op_argv("nextseek-api-write", parser_plan="{}", confirmed_write=True)
    assert argv[-1] == "--confirmed-write" or "--confirmed-write" in argv


def test_report_argv():
    argv = gate.build_op_argv("nextseek-report", mode="samples", project="1-sandbox")
    assert argv == ["nextseek-report", "--mode", "samples", "--project", "1-sandbox"]


def test_report_requires_mode_and_project():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-report", mode="samples")
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-report", project="1-sandbox")


def test_generate_submission_argv():
    argv = gate.build_op_argv("nextseek-generate-submission", submission_type="GEO", uids="S0001,S0002")
    assert argv == ["nextseek-generate-submission", "--type", "GEO", "--uids", "S0001,S0002"]


def test_unknown_op_rejected():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-not-a-real-op", query="x")


def test_build_op_argv_covers_every_bin_op():
    """Every query-family bin op must be dispatchable (no silent gaps)."""
    kwargs_for_op = {
        "nextseek-entity-extract": {"query": "q"},
        "nextseek-parse": {"query": "q"},
        "nextseek-graph": {"query": "q"},
        "nextseek-plan": {"query": "q"},
        "nextseek-query": {"query": "q"},
        "nextseek-recall": {"turn": 1},
        "nextseek-api-read": {"parser_plan": "{}"},
        "nextseek-api-write": {"parser_plan": "{}"},
        "nextseek-report": {"mode": "samples", "project": "1-sandbox"},
        "nextseek-generate-submission": {"submission_type": "GEO", "uids": "S1"},
    }
    assert set(kwargs_for_op) == set(BIN_OPS)
    for op, kwargs in kwargs_for_op.items():
        argv = gate.build_op_argv(op, **kwargs)
        assert argv[0] == op
        assert all(isinstance(a, str) for a in argv)


# ==========================================================================
# gate_executor_environment / build_gate_executor_run_kwargs
# ==========================================================================

def test_gate_executor_environment_goes_through_build_agent_environment():
    """iter-3 M-1: the harness must not hand-assemble the env -- it must
    call cc_engine.build_agent_environment (proven here by checking every
    key that function contributes is present, plus the harness's own
    additive idle-mode flag)."""
    hostile_source = {
        "AWS_BEARER_TOKEN_BEDROCK": "ABSK-SHOULD-NEVER-APPEAR",
        "NEO4J_PASSWORD": "shared-secret",
        "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000",
    }
    env = gate.gate_executor_environment(
        api_user="gateuser", api_pass="gatepass", source=hostile_source,
    )
    expected = cc_engine.build_agent_environment(
        source=hostile_source, api_user="gateuser", api_pass="gatepass", path_mappings={},
    )
    for k, v in expected.items():
        assert env[k] == v
    assert env["DMAC_RUNTIME_MODE"] == "idle"
    # No shared cred key the builder itself excludes can appear via the
    # harness's additive step either.
    for key in SHARED_CRED_KEYS:
        assert key not in env


def test_gate_executor_environment_carries_no_shared_creds_even_from_hostile_source():
    env = gate.gate_executor_environment(
        api_user="u", api_pass="p",
        source={"AWS_BEARER_TOKEN_BEDROCK": "x", "MYSQL_PASSWORD": "y", "GCP_API_KEY": "z"},
    )
    for key in SHARED_CRED_KEYS:
        assert key not in env


def test_build_gate_executor_run_kwargs_shape():
    env = {"NEXTSEEK_SIDECAR_HOST": "nextseek-sidecar", "DMAC_RUNTIME_MODE": "idle"}
    kwargs = gate.build_gate_executor_run_kwargs(run_id="deadbeef", image="dmac-assistant:poc", environment=env)
    assert kwargs["name"] == matrix_executor_name("deadbeef") == "dmac-cc-matrix-deadbeef"
    assert kwargs["image"] == "dmac-assistant:poc"
    assert kwargs["environment"] is env
    assert kwargs["network"] == cc_engine.DEFAULT_NETWORK
    assert kwargs["labels"]["nextseek.cc.run"] == "deadbeef"
    assert kwargs["labels"]["nextseek.cc.matrix"] == "1"
    assert kwargs["detach"] is True


# ==========================================================================
# make_matrix_row
# ==========================================================================

def test_make_matrix_row_success_excerpts_stdout():
    result = gate.ExecResult(exit_code=0, stdout='{"op": "entity", "result": {}}', stderr="", wall_secs=1.234567)
    row = gate.make_matrix_row(
        "nextseek-entity-extract", result=result, container_id="cid1",
        container_name="dmac-cc-matrix-run1", image="dmac-assistant:poc", transport="sidecar",
    )
    assert row["op"] == "nextseek-entity-extract"
    assert row["exit_code"] == 0
    assert json.loads(row["excerpt"]) == {"op": "entity", "result": {}}
    assert row["container_id"] == "cid1"
    assert row["container_name"] == "dmac-cc-matrix-run1"
    assert row["image"] == "dmac-assistant:poc"
    assert row["wall_secs"] == pytest.approx(1.235, abs=1e-3)
    assert "published_path" not in row


def test_make_matrix_row_failure_excerpts_stderr():
    result = gate.ExecResult(
        exit_code=5, stdout="", stderr='{"error": {"code": "WRITE_BLOCKED", "message": "x"}}', wall_secs=0.5,
    )
    row = gate.make_matrix_row(
        "nextseek-api-write", result=result, container_id="cid1",
        container_name="dmac-cc-matrix-run1", image="dmac-assistant:poc", transport="sidecar",
    )
    assert row["exit_code"] == 5
    assert "WRITE_BLOCKED" in row["excerpt"]


def test_make_matrix_row_carries_published_path_when_given():
    result = gate.ExecResult(exit_code=0, stdout='{"op": "report", "result": {}}', stderr="", wall_secs=2.0)
    row = gate.make_matrix_row(
        "nextseek-report", result=result, container_id="cid1", container_name="dmac-cc-matrix-run1",
        image="dmac-assistant:poc", transport="sidecar",
        published_path="/dmac/users/1-sandbox/gateuser/scratch/nextseek-artifacts/report.xlsx",
    )
    assert row["published_path"].endswith("report.xlsx")


def test_transport_for_op_matches_wire_topology():
    from nextseek_api.cc_assistant.bin_inventory import is_viewset_op

    for op in BIN_OPS:
        expected = "viewset" if is_viewset_op(op) else "sidecar"
        assert gate.TRANSPORT_FOR_OP[op] == expected
    assert gate.TRANSPORT_FOR_OP["nextseek-query"] == "viewset"
    assert gate.TRANSPORT_FOR_OP["nextseek-plan"] == "viewset"
    assert gate.TRANSPORT_FOR_OP["nextseek-recall"] == "viewset"


# ==========================================================================
# Sweep command / post-sweep scan command construction
# ==========================================================================

def test_build_sweep_command_matches_the_documented_invocation_form():
    argv = gate.build_sweep_command(
        nextseek_container="nextseek", user_id="gateuser", api_user="gateuser", project="1-sandbox",
    )
    assert argv == [
        "docker", "exec", "nextseek", "/app/.venv/bin/python", "manage.py", "cc_sweep_staging",
        "--user-id", "gateuser", "--api-user", "gateuser", "--project", "1-sandbox",
    ]


def test_build_ps_name_filter_command():
    argv = gate.build_ps_name_filter_command("nextseek_nginx")
    assert argv == ["docker", "ps", "--filter", "name=nextseek_nginx", "--format", "{{.Names}}"]


def test_build_post_sweep_scan_command_mounts_readonly():
    argv = gate.build_post_sweep_scan_command(volume="dmac-cc-users")
    assert argv[0] == "docker"
    assert "-v" in argv
    assert "dmac-cc-users:/v:ro" in argv


# ==========================================================================
# images_json_cc_image
# ==========================================================================

def test_images_json_cc_image_reads_the_pinned_key(tmp_path):
    (tmp_path / "images.json").write_text(json.dumps({"cc-agent": "dmac-assistant:poc"}), encoding="utf-8")
    assert gate.images_json_cc_image(tmp_path) == "dmac-assistant:poc"


def test_images_json_cc_image_missing_file_returns_none(tmp_path):
    assert gate.images_json_cc_image(tmp_path) is None


def test_images_json_cc_image_missing_key_returns_none(tmp_path):
    (tmp_path / "images.json").write_text(json.dumps({"nextseek": "x"}), encoding="utf-8")
    assert gate.images_json_cc_image(tmp_path) is None


# ==========================================================================
# write_json / write_text
# ==========================================================================

def test_write_json_and_text_roundtrip(tmp_path):
    gate.write_json(tmp_path / "a.json", {"x": 1})
    assert json.loads((tmp_path / "a.json").read_text()) == {"x": 1}
    gate.write_text(tmp_path / "b.txt", "hello\n")
    assert (tmp_path / "b.txt").read_text() == "hello\n"


# ==========================================================================
# Seeded fixture (Step 3b): creation is the PRIMARY behavior -- greenfield
# works from zero, via an injected fake HTTP layer (zero live calls).
# ==========================================================================

SAMPLE_TYPES_BODY = {"data": [{"id": "12", "type": "sample_types", "attributes": {"title": "Generic"}}]}
EMPTY_LIST_BODY = {"data": []}


class FakeHttp:
    """A fake ``(method, path, json_body=None, params=None) -> (status, body)``
    HTTP layer. ``routes`` maps (method, path) -> a response or an ordered
    list of responses (popped per call). Records every call + payload."""

    def __init__(self, routes):
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, path, json_body=None, params=None):
        self.calls.append((method, path, json_body))
        responses = self.routes.get((method, path))
        if not responses:
            return 404, {"errors": [{"title": f"no fake route for {method} {path}"}]}
        resp = responses.pop(0) if len(responses) > 1 else responses[0]
        return resp


def _sample_created_body(seek_id, uid=None):
    attrs = {"title": f"sample {seek_id}"}
    if uid:
        attrs["attribute_map"] = {"UID": uid}
    return 201, {"data": {"id": str(seek_id), "type": "samples", "attributes": attrs}}


def _greenfield_http():
    return FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, EMPTY_LIST_BODY),
        ("POST", gate.PROJECTS_ENDPOINT): (
            201, {"data": {"id": "42", "type": "projects",
                            "attributes": {"title": gate.DEFAULT_GATE_PROJECT_TITLE}}},
        ),
        ("GET", gate.SAMPLE_TYPES_ENDPOINT): (200, SAMPLE_TYPES_BODY),
        ("POST", gate.SAMPLES_ENDPOINT): [
            _sample_created_body(101, uid="SMP-101"),
            _sample_created_body(102, uid="SMP-102"),
        ],
    })


def test_seeded_fixture_greenfield_creates_project_and_samples():
    http = _greenfield_http()
    fx = gate.create_seeded_fixture(
        assistant_base_url="http://nextseek_nginx", api_user="gateuser", api_pass="x",
        run_id="deadbeef", http=http,
    )
    # PRIMARY behavior: from zero, the project WAS created...
    methods = [(m, p) for m, p, _ in http.calls]
    assert ("POST", gate.PROJECTS_ENDPOINT) in methods
    # ...and both samples were created against it, sample_type resolved live.
    sample_posts = [b for m, p, b in http.calls if (m, p) == ("POST", gate.SAMPLES_ENDPOINT)]
    assert len(sample_posts) == gate.DEFAULT_FIXTURE_SAMPLE_COUNT
    for body in sample_posts:
        rel = body["data"]["relationships"]
        assert rel["sample_type"]["data"] == {"type": "sample_types", "id": "12"}
        assert rel["projects"]["data"] == [{"type": "projects", "id": "42"}]
    # dirname = {pid}-{slug} from the CREATED project's real id (engine helpers).
    assert fx.project == "42-cc-capability-gate-sandbox"
    assert fx.seek_project_id == "42"
    assert fx.uids == ["SMP-101", "SMP-102"]
    assert fx.title == gate.DEFAULT_GATE_PROJECT_TITLE
    # Requests used are recorded, per the Step 3b clause.
    assert len(fx.requests) == 5  # GET projects, POST project, GET sample_types, 2x POST samples
    assert any("POST /nextseek_api/projects/" in r for r in fx.requests)
    assert sum("POST /nextseek_api/samples/" in r for r in fx.requests) == 2


def test_seeded_fixture_reuses_existing_project_as_secondary_idempotence_path():
    http = FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, {"data": [
            {"id": "7", "type": "projects", "attributes": {"title": "Unrelated"}},
            {"id": "42", "type": "projects",
             "attributes": {"title": gate.DEFAULT_GATE_PROJECT_TITLE}},
        ]}),
        ("GET", gate.SAMPLE_TYPES_ENDPOINT): (200, SAMPLE_TYPES_BODY),
        ("POST", gate.SAMPLES_ENDPOINT): [
            _sample_created_body(201, uid="SMP-201"),
            _sample_created_body(202, uid="SMP-202"),
        ],
    })
    fx = gate.create_seeded_fixture(
        assistant_base_url="http://nextseek_nginx", api_user="gateuser", api_pass="x", http=http,
    )
    # SECONDARY path: no project POST when a prior gate run left the sandbox.
    assert ("POST", gate.PROJECTS_ENDPOINT) not in [(m, p) for m, p, _ in http.calls]
    assert fx.seek_project_id == "42"
    # Samples are STILL created fresh (creation stays primary for the data).
    assert fx.uids == ["SMP-201", "SMP-202"]


def test_seeded_fixture_project_create_failure_raises():
    http = FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, EMPTY_LIST_BODY),
        ("POST", gate.PROJECTS_ENDPOINT): (403, {"errors": [{"title": "forbidden"}]}),
    })
    with pytest.raises(gate.FixtureCreationError, match="project create failed"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http,
        )


def test_seeded_fixture_no_sample_types_raises_environment_prerequisite():
    http = FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, EMPTY_LIST_BODY),
        ("POST", gate.PROJECTS_ENDPOINT): (201, {"data": {"id": "42", "type": "projects", "attributes": {}}}),
        ("GET", gate.SAMPLE_TYPES_ENDPOINT): (200, EMPTY_LIST_BODY),
    })
    with pytest.raises(gate.FixtureCreationError, match="no sample_type available"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http,
        )


def test_seeded_fixture_sample_create_failure_raises():
    http = FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, EMPTY_LIST_BODY),
        ("POST", gate.PROJECTS_ENDPOINT): (201, {"data": {"id": "42", "type": "projects", "attributes": {}}}),
        ("GET", gate.SAMPLE_TYPES_ENDPOINT): (200, SAMPLE_TYPES_BODY),
        ("POST", gate.SAMPLES_ENDPOINT): (422, {"errors": [{"title": "Invalid request"}]}),
    })
    with pytest.raises(gate.FixtureCreationError, match="sample create failed"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http,
        )


def test_seeded_fixture_project_create_without_id_raises():
    http = FakeHttp({
        ("GET", gate.PROJECTS_ENDPOINT): (200, EMPTY_LIST_BODY),
        ("POST", gate.PROJECTS_ENDPOINT): (201, {"data": {"type": "projects", "attributes": {}}}),
    })
    with pytest.raises(gate.FixtureCreationError, match="no data.id"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http,
        )


def test_binding_fixture_record_passes_validator_check(tmp_path):
    """Writer output must satisfy ``check_gate_instance_binding_present``."""
    from nextseek_api.cc_assistant import step7_gate_catalog as catalog
    from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
        Context,
        check_gate_instance_binding_present,
    )
    binding = catalog.InstanceBinding(
        binding_id="hermetic",
        project_id=1,
        project_title="Published Data",
        sample_count=100,
        sample_type_count=10,
        reference_uids=["A.ADCD-250312ALT-1-PUB"],
        cc_project_dirname="1-published-data",
        forbidden_actions=["create_seeded_fixture"],
    )
    gate.write_json(tmp_path / "instance_binding.json", catalog.binding_fixture_record(binding))
    ctx = Context(run_dir=tmp_path, preflight=None, meta={}, repo_root=tmp_path)
    name, ok, detail = check_gate_instance_binding_present(ctx)
    assert ok is True, detail


def test_extract_sample_uid_prefers_attribute_map_uid_over_seek_id():
    _, body = _sample_created_body(300, uid="SMP-300")
    assert gate.extract_sample_uid(body) == "SMP-300"


def test_extract_sample_uid_falls_back_to_jsonapi_id():
    _, body = _sample_created_body(301)  # no attribute_map UID
    assert gate.extract_sample_uid(body) == "301"


def test_find_project_id_by_title_exact_match_only():
    body = {"data": [{"id": "9", "type": "projects", "attributes": {"title": "CC Capability Gate Sandbox EXTRA"}}]}
    assert gate.find_project_id_by_title(body, "CC Capability Gate Sandbox") is None
    assert gate.find_project_id_by_title(None, "x") is None


def test_first_sample_type_id():
    assert gate.first_sample_type_id(SAMPLE_TYPES_BODY) == "12"
    assert gate.first_sample_type_id(EMPTY_LIST_BODY) is None
    assert gate.first_sample_type_id(None) is None


# ==========================================================================
# Drift-pins: the payload builders' contract vs the ACTUAL server-side
# request models + viewset create actions (nextseek_api.models needs
# configured Django settings, so the pin reads the source -- this repo's
# established cross-file literal-lock pattern).
# ==========================================================================

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_drift_pin_sample_create_contract_in_models_source():
    src = (_REPO_ROOT / "nextseek_api" / "models.py").read_text(encoding="utf-8")
    # SamplePostAttributes: title is the required attribute the builder emits.
    assert "class SamplePostAttributes(BaseModel):\n    title: str" in src
    # SamplePostRelationships: sample_type REQUIRED, projects Optional.
    assert "class SamplePostRelationships(BaseModel):\n    sample_type: SingleReference" in src
    assert "projects: Optional[MultipleReferences]" in src
    # The JSON:API type literal the builder pins.
    assert "type: Literal['samples']" in src
    assert "class SampleCreateRequest(BaseModel):" in src


def test_drift_pin_project_create_contract_in_models_source():
    src = (_REPO_ROOT / "nextseek_api" / "models.py").read_text(encoding="utf-8")
    assert "class ProjectPostAttributes(BaseModel):\n    title: str" in src
    assert "type: Literal['projects']" in src
    assert "class ProjectCreateRequest(BaseModel):" in src
    # relationships are Optional on ProjectPostData -- the builder may omit them.
    assert "relationships: Optional[ProjectPostRelationships] = None" in src


def test_drift_pin_create_viewset_actions_exist():
    samples_src = (_REPO_ROOT / "nextseek_api" / "services" / "samples.py").read_text(encoding="utf-8")
    projects_src = (_REPO_ROOT / "nextseek_api" / "services" / "projects.py").read_text(encoding="utf-8")
    urls_src = (_REPO_ROOT / "nextseek_api" / "urls.py").read_text(encoding="utf-8")
    assert "SampleCreateRequest.model_validate(request.data)" in samples_src
    assert "ProjectCreateRequest.model_validate(request.data)" in projects_src
    # DRF DefaultRouter create routes: POST /nextseek_api/samples/ and
    # /nextseek_api/projects/ exist because these viewsets are registered.
    assert 'router.register(r"samples", views.SampleViewSet' in urls_src
    assert 'router.register(r"projects", views.ProjectViewSet' in urls_src
    assert 'router.register(r"sample_types", views.SampleTypeViewSet' in urls_src


def test_payload_builders_emit_exactly_the_declared_shapes():
    """extra='forbid' on the server models: the builders must emit EXACTLY
    the declared key sets, nothing more."""
    p = gate.build_project_create_payload("T")
    assert set(p) == {"data"}
    assert set(p["data"]) == {"type", "attributes"}
    assert p["data"]["type"] == "projects"
    assert set(p["data"]["attributes"]) == {"title"}

    s = gate.build_sample_create_payload("T", "12", "42")
    assert set(s) == {"data"}
    assert set(s["data"]) == {"type", "attributes", "relationships"}
    assert s["data"]["type"] == "samples"
    assert set(s["data"]["attributes"]) == {"title"}
    assert set(s["data"]["relationships"]) == {"sample_type", "projects"}

    s_no_proj = gate.build_sample_create_payload("T", "12")
    assert set(s_no_proj["data"]["relationships"]) == {"sample_type"}


# ==========================================================================
# Import-safety: module-level constants agree with the validator's contract
# ==========================================================================

def test_harness_bin_ops_matches_validator_bin_ops():
    assert gate.BIN_OPS == BIN_OPS


def test_harness_exposes_all_public_names():
    for name in gate.__all__:
        assert hasattr(gate, name), name
