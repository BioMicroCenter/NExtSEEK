from __future__ import annotations

import json
import pathlib
import hashlib
import sys

import orjson
import polars as pl
import pytest

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
import _batch_upload_runner as runner


UID = "D.IMG-230913ENG-1757-PUB"
TITLE = "Comet Chip Analysis - Data Attached"
SCHEMA = [
    {
        "sample_type": "TIS",
        "attributes": [
            {"title": "UID", "required": True, "is_title": True},
            {"title": "Name", "required": True, "is_title": False},
            {"title": "Scientist", "required": True, "is_title": False},
            {"title": "Parent", "required": False, "is_title": False},
            {"title": "Treatment", "required": False, "is_title": False},
        ],
    }
]
FIXTURES = pathlib.Path("tests/unit/fixtures/ns")


class StubClient:
    last = None

    def __init__(self, *, validate=None, search_rows=None, samples=None, schema=None,
                 projects=None):
        type(self).last = self
        self.validate_response = validate or _valid(1)
        self.search_rows = search_rows
        self.samples = samples or {351: {"324503"}, 260: set()}
        self.schema = schema or SCHEMA[0]
        self.projects = projects
        self.calls = []
        self.validated_path = None

    @classmethod
    def from_env(cls, *, transport=None):
        if isinstance(transport, StubClient):
            return transport
        return cls()

    def sample_type_attributes(self, sample_type):
        self.calls.append(("schema", sample_type))
        return self.schema

    def list_projects(self):
        if self.projects is not None:
            return self.projects
        return [{"id": "1", "attributes": {"title": "P"}}]

    def list_assays(self):
        return {TITLE: [351, 260], "RNA-seq": [9], "Imaging": [2], "Solo": [400]}

    def project_assays(self, project_id):
        return {351, 260, 9, 2, 400}

    def resolve_assay_title(self, title, title_map, project_assay_ids):
        candidates = [item for item in title_map.get(title, []) if item in project_assay_ids]
        if len(candidates) != 1:
            raise ValueError("ambiguous")
        return candidates[0]

    def search_samples_by_uid(self, uids, *, known_assay_titles=None):
        self.calls.append(("search", list(uids), tuple(sorted(known_assay_titles or []))))
        if self.search_rows is not None:
            return self.search_rows
        return [
            {
                "id": 324503,
                "numeric_seek_id": 324503,
                "json_metadata": {"UID": UID},
                "assays": TITLE,
                "assay_titles": [TITLE],
            }
        ]

    def assay_samples(self, assay_ids):
        self.calls.append(("assay_samples", sorted(assay_ids)))
        return {int(assay_id): set(self.samples.get(int(assay_id), set())) for assay_id in assay_ids}

    def resolve_current_assay_titles(self, titles, *, sample_numeric_id, title_map, project_assay_ids):
        ambiguous = []
        resolved = set()
        for title in titles:
            candidates = [item for item in title_map.get(title, []) if item in project_assay_ids]
            if not candidates:
                raise ValueError("not accessible")
            if len(candidates) == 1:
                resolved.add(candidates[0])
            else:
                ambiguous.extend(candidates)
        if ambiguous:
            by_assay = self.assay_samples(set(ambiguous))
            matched = {aid for aid in ambiguous if str(sample_numeric_id) in by_assay.get(aid, set())}
            if not matched:
                raise ValueError("unresolved")
            resolved |= matched
        return resolved

    def validate_file(self, path, *, project_id, checks):
        self.calls.append(("validate_file", pathlib.Path(path), project_id, checks))
        self.validated_path = pathlib.Path(path)
        self.validated_hash = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
        return self.validate_response


def _valid(processed=1):
    return {
        "valid": True,
        "errors": [],
        "checks_run": ["structure", "name_check", "dag"],
        "totals": {"processed": processed},
    }


def _rows_file(tmp_path, rows):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows))
    return path


def _confirm_file(tmp_path, *, project_id=1, confirmed=True, accessible=(1,)):
    path = tmp_path / "project.json"
    path.write_text(
        json.dumps(
            {
                "project_id": project_id,
                "confirmed": confirmed,
                "accessible_project_ids": list(accessible),
            }
        )
    )
    return path


def _schema_file(tmp_path, schema=SCHEMA):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema))
    return path


def _titles_file(tmp_path, mapping=None):
    path = tmp_path / "id_to_title.json"
    path.write_text(json.dumps(mapping or {351: TITLE, 260: TITLE, 9: "RNA-seq", 2: "Imaging"}))
    return path


def _current_file(tmp_path, mapping=None):
    path = tmp_path / "current.json"
    path.write_text(json.dumps(mapping or {UID: [351]}))
    return path


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _create_row(**extra):
    row = {"UID": "", "SampleType": "TIS", "attributes": {"Name": "sample", "Scientist": "Curator"}}
    row.update(extra)
    return row


def _update_row(**extra):
    row = {"UID": UID, "SampleType": "TIS", "attributes": {"Treatment": "drug"}, "assay_ids": []}
    row.update(extra)
    return row


def _run(tmp_path, client, rows, *extra):
    return runner.main(
        [
            "build-validate",
            "--rows",
            str(_rows_file(tmp_path, rows)),
            "--project-id",
            "1",
            "--project-confirmation",
            str(_confirm_file(tmp_path)),
            "--out",
            str(tmp_path / "scratch"),
            *extra,
        ],
        transport=client,
    )


def _last_gate(capsys):
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("{")]
    return json.loads(lines[-1])


def _artifact(tmp_path):
    return next((tmp_path / "scratch").glob("payload_flat.xlsx"))


def _sheet(path):
    df = pl.read_excel(path, sheet_name="Samples", engine="calamine")
    return [{column: df[column][idx] for column in df.columns} for idx in range(df.height)]


def test_gate_a_content_bad_reaches_validate_then_refuses(tmp_path):
    client = StubClient(validate=_valid(1))
    artifact = tmp_path / "payload_flat.xlsx"
    _write_artifact(
        artifact,
        [{"UID": UID, "json_metadata": {"UID": UID, "Treatment": "drug", "Parent": ""}, "assay_ids": [351]}],
    )
    validation = client.validate_file(artifact, project_id=1, checks="structure,name_check,dag")
    with pytest.raises(runner.GateError, match="present_blank"):
        runner._artifact_gate(
            artifact=artifact,
            validation=validation,
            manifest={UID: {"retrieve_status": "verified", "current_assay_ids": [351]}},
        )
    assert any(call[0] == "validate_file" for call in client.calls)


def test_gate_a2_valid_false(tmp_path, capsys):
    rc = _run(tmp_path, StubClient(validate={**_valid(1), "valid": False}), [_create_row()])
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "server_valid"


def test_gate_b_missing_checks(tmp_path, capsys):
    rc = _run(tmp_path, StubClient(validate={**_valid(1), "checks_run": ["structure"]}), [_create_row()])
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "checks_run"


def test_gate_c_totals_mismatch(tmp_path, capsys):
    rc = _run(tmp_path, StubClient(validate=_valid(0)), [_create_row()])
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "processed"
    rc_missing = _run(tmp_path, StubClient(validate={k: v for k, v in _valid(1).items() if k != "totals"}), [_create_row()])
    assert rc_missing != 0
    assert _last_gate(capsys)["gate"] == "processed"


def test_gate_d_staged_hash_equals_promoted(tmp_path):
    client = StubClient(validate=_valid(1))
    assert _run(tmp_path, client, [_create_row(assay_ids=[9])]) == 0
    promoted = _artifact(tmp_path)
    assert client.validated_path is not None
    assert client.validated_hash == runner._sha256(promoted)
    assert runner.SCRATCH_ROOT not in client.validated_path.parents


def test_gate_e_no_scratch_on_failure(tmp_path, capsys):
    rc = _run(tmp_path, StubClient(validate={**_valid(1), "valid": False}), [_create_row()])
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "server_valid"
    assert not list((tmp_path / "scratch").glob("*.xlsx"))


def test_gate_e2_validate_file_mode(tmp_path, capsys):
    client = StubClient(validate=_valid(1))
    assert _run(tmp_path, client, [_create_row()]) == 0
    call = next(call for call in client.calls if call[0] == "validate_file")
    assert call[1].name == "payload_flat.xlsx"
    assert call[3] == "structure,name_check,dag"
    rc = runner.main(
        [
            "build-payload",
            "--rows", str(_rows_file(tmp_path, [_create_row()])),
            "--schema", str(_schema_file(tmp_path)),
            "--id-to-title", str(_titles_file(tmp_path)),
            "--out", "/data/scratch/out",
        ]
    )
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "staging"


def test_gate_f_absent_project_id(tmp_path, capsys):
    rows = _rows_file(tmp_path, [_create_row()])
    rc = runner.main(["build-validate", "--rows", str(rows), "--out", str(tmp_path / "scratch")], transport=StubClient())
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "project_id"
    assert _run(tmp_path, StubClient(validate=_valid(1)), [_create_row()]) == 0
    assert _run(tmp_path, StubClient(validate=_valid(1)), [_update_row(attributes={"Treatment": "drug"})]) == 0


def test_gate_f2_unverified_project_id(tmp_path, capsys):
    rows = _rows_file(tmp_path, [_create_row()])
    confirm = _confirm_file(tmp_path, confirmed=False)
    rc = runner.main(
        ["build-validate", "--rows", str(rows), "--project-id", "1", "--project-confirmation", str(confirm), "--out", str(tmp_path / "scratch")],
        transport=StubClient(),
    )
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "project_unconfirmed"
    rc_empty_schema = runner.main(
        [
            "build-validate",
            "--rows", str(rows),
            "--project-id", "1",
            "--project-confirmation", str(_confirm_file(tmp_path)),
            "--out", str(tmp_path / "scratch2"),
        ],
        transport=StubClient(schema={"sample_type": "TIS", "attributes": []}),
    )
    assert rc_empty_schema != 0
    assert _last_gate(capsys)["gate"] == "schema"
    rc_weak_schema = runner.main(
        [
            "build-validate",
            "--rows", str(rows),
            "--project-id", "1",
            "--project-confirmation", str(_confirm_file(tmp_path)),
            "--out", str(tmp_path / "scratch3"),
        ],
        transport=StubClient(schema={"sample_type": "TIS", "attributes": [{"title": "Name"}]}),
    )
    assert rc_weak_schema != 0
    assert _last_gate(capsys)["gate"] == "schema"


def test_gate_g_project_confirmation_token(tmp_path):
    assert runner._read_confirmation(_confirm_file(tmp_path), 1)["confirmed"] is True
    with pytest.raises(runner.GateError):
        runner._read_confirmation(_confirm_file(tmp_path, project_id=2), 1)
    with pytest.raises(runner.GateError, match="project_unconfirmed"):
        runner._read_confirmation(None, 1)


def test_gate_h_confirmed_project_accessible(tmp_path):
    with pytest.raises(runner.GateError):
        runner._read_confirmation(_confirm_file(tmp_path, accessible=(2,)), 1)
    client = StubClient()
    title_map, project_ids, _ = runner._assay_maps(client, 1)
    assert client.resolve_assay_title("Solo", title_map, project_ids) == 400
    with pytest.raises(ValueError):
        client.resolve_assay_title(TITLE, title_map, project_ids)
    client.project_assays = lambda project_id: {351}
    title_map, project_ids, _ = runner._assay_maps(client, 1)
    assert client.resolve_assay_title(TITLE, title_map, project_ids) == 351


def test_gate_j_assay_subset_refused(tmp_path):
    artifact = tmp_path / "payload_flat.xlsx"
    _write_artifact(artifact, [{"UID": UID, "json_metadata": {"UID": UID, "Treatment": "drug"}, "assay_ids": [9]}])
    with pytest.raises(runner.GateError, match="assay_superset"):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={UID: {"retrieve_status": "verified", "current_assay_ids": [351]}})
    with pytest.raises(runner.GateError, match="manifest"):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={})
    runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={UID: {"retrieve_status": "verified", "current_assay_ids": [351]}}, confirm_clear_assays={UID})

    _write_artifact(
        artifact,
        [{"UID": UID, "json_metadata": {"UID": UID, "Treatment": "drug"}, "assay_ids": []}],
    )
    with pytest.raises(runner.GateError, match="manifest"):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={})


def test_gate_k_confirm_clear_assays_scoped(tmp_path):
    artifact = tmp_path / "payload_flat.xlsx"
    _write_artifact(artifact, [{"UID": UID, "json_metadata": {"UID": UID}, "assay_ids": []}])
    manifest = {UID: {"retrieve_status": "verified", "current_assay_ids": [351]}}
    runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest=manifest, confirm_clear_assays={UID})
    with pytest.raises(runner.GateError):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest=manifest, confirm_clear_assays={"other"})
    _write_artifact(
        artifact,
        [
            {
                "UID": "",
                "SampleType": "TIS",
                "json_metadata": {"Scientist": "Curator"},
                "REVIEW_ONLY__Name": "sample",
                "assay_ids": [],
            }
        ],
    )
    with pytest.raises(runner.GateError, match="required_missing"):
        runner._artifact_gate(
            artifact=artifact,
            validation=_valid(1),
            manifest={},
            sample_type_attributes=SCHEMA,
        )


def test_gate_l_retrieve_failure_refused(tmp_path, capsys):
    rc = _run(tmp_path, StubClient(search_rows=[]), [_update_row(assay_ids=[9])])
    assert rc != 0
    assert _last_gate(capsys)["gate"] == "manifest"


def test_gate_m_manifest_schema(tmp_path):
    manifest = runner._resolve_manifest([_update_row(assay_ids=[9])], StubClient(), {TITLE: [351, 260]}, {351, 260})
    assert manifest[UID]["retrieve_status"] == "verified"
    assert manifest[UID]["current_assay_ids"] == [351]
    comma = StubClient(
        search_rows=[
            {
                "json_metadata": {"UID": UID},
                "id": 324503,
                "numeric_seek_id": 324503,
                "assays": "Alpha, with comma",
                "assay_titles": ["Alpha, with comma"],
            }
        ]
    )
    manifest = runner._resolve_manifest([_update_row(assay_ids=[9])], comma, {"Alpha, with comma": [400]}, {400})
    assert manifest[UID]["current_assay_ids"] == [400]
    degraded = runner._resolve_manifest([_update_row(assay_ids=[9])], comma, {}, set())
    assert degraded[UID]["retrieve_status"] == "degraded"


def test_gate_n_metadata_only_carries_forward(tmp_path):
    client = StubClient(validate=_valid(1))
    assert _run(tmp_path, client, [_update_row(attributes={"Treatment": "drug"}, assay_ids=[])]) == 0
    row = _sheet(_artifact(tmp_path))[0]
    assert orjson.loads(row["json_metadata"]) == {"Treatment": "drug", "UID": UID}
    assert 351 in orjson.loads(row["assay_ids"])


def test_gate_o_degraded_manifest_refused_zero_assay_passes(tmp_path, capsys):
    degraded = StubClient(search_rows=[{"json_metadata": {"UID": UID}, "id": 1, "numeric_seek_id": 1}])
    assert _run(tmp_path, degraded, [_update_row(assay_ids=[9])]) != 0
    assert _last_gate(capsys)["gate"] == "manifest"
    zero_row = _fixture("advanced_search_zero_assay.json")["rows"][0]
    zero_row = {
        **zero_row,
        "numeric_seek_id": zero_row["id"],
        "assay_titles": [],
    }
    zero_uid = zero_row["json_metadata"]["UID"]
    zero = StubClient(search_rows=[zero_row])
    assert _run(tmp_path, zero, [_update_row(UID=zero_uid, assay_ids=[9])]) == 0


def test_gate_p_shrink_without_confirm_refused(tmp_path):
    artifact = tmp_path / "payload_flat.xlsx"
    _write_artifact(artifact, [{"UID": UID, "json_metadata": {"UID": UID}, "assay_ids": []}])
    with pytest.raises(runner.GateError):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={UID: {"retrieve_status": "verified", "current_assay_ids": [351]}})


def test_gate_q_path2_resolves_351_not_260(tmp_path):
    client = StubClient(samples={351: {"324503"}, 260: set()})
    manifest = runner._resolve_manifest([_update_row(assay_ids=[9])], client, {TITLE: [351, 260]}, {351, 260})
    assert manifest[UID]["current_assay_ids"] == [351]
    assert ("assay_samples", [260, 351]) in client.calls
    assert not any("samples/" in str(call) for call in client.calls)


def test_gate_q2_carry_both(tmp_path):
    both = {int(key): set(value) for key, value in _fixture("assay_samples_both.json")["assay_samples"].items()}
    client = StubClient(samples=both, validate=_valid(1))
    assert _run(tmp_path, client, [_update_row()]) == 0
    row = _sheet(_artifact(tmp_path))[0]
    assert set(orjson.loads(row["assay_ids"])) >= {351, 260}
    assert orjson.loads(row["assay_titles"]).count(TITLE) == 2


def test_gate_r_truncation(tmp_path, capsys):
    long = StubClient(search_rows=[{"json_metadata": {"UID": UID}, "id": 1, "numeric_seek_id": 1, "assays": "x" * 900, "assay_titles": []}])
    assert _run(tmp_path, long, [_update_row(assay_ids=[9])]) != 0
    assert _last_gate(capsys)["gate"] == "manifest"
    short = StubClient(search_rows=[{"json_metadata": {"UID": UID}, "id": 1, "numeric_seek_id": 1, "assays": "", "assay_titles": []}])
    assert _run(tmp_path, short, [_update_row(assay_ids=[9])]) == 0


def test_gate_s_create_addition_resolve(tmp_path):
    client = StubClient(validate=_valid(1))
    assert _run(tmp_path, client, [_create_row(assay_titles=["RNA-seq"])]) == 0
    assert 9 in orjson.loads(_sheet(_artifact(tmp_path))[0]["assay_ids"])


def test_gate_t_update_addition_resolve(tmp_path):
    client = StubClient(validate=_valid(1))
    assert _run(tmp_path, client, [_update_row(assay_titles=["RNA-seq"])]) == 0
    ids = set(orjson.loads(_sheet(_artifact(tmp_path))[0]["assay_ids"]))
    assert {9, 351} <= ids


def test_attrs_extract_and_resolution_commands(tmp_path, capsys, monkeypatch):
    assert runner.main(["attrs", "--type", "TIS"], transport=StubClient()) == 0
    assert json.loads(capsys.readouterr().out)["sample_type"] == "TIS"
    monkeypatch.setattr(runner, "extract_text", lambda path, fallback=None: "text")
    assert runner.main(["extract", "--file", str(tmp_path / "x.pdf")]) == 0
    assert "text" in capsys.readouterr().out
    out = tmp_path / "project.json"
    assert runner.main(["project-resolve", "--project-id", "1", "--confirmed", "--out", str(out)], transport=StubClient()) == 0
    assert runner.main(["assay-resolve", "--project-id", "1", "--title", "Solo"], transport=StubClient()) == 0
    assert runner.main(["sample-search", "--uid", UID], transport=StubClient()) == 0
    assert runner.main(["assay-resolve", "--project-id", "1", "--title", TITLE], transport=StubClient()) != 0
    gate = _last_gate(capsys)
    assert gate == {"gate": "assay_resolution", "detail": TITLE}


_NAMED_PROJECTS = [
    {"id": "1", "attributes": {"title": "Published Data"}},
    {"id": "2", "attributes": {"title": "Other Project"}},
]


def test_project_resolve_by_name_confirms(tmp_path):
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--name", "Published Data", "--confirmed", "--out", str(out)],
        transport=StubClient(projects=_NAMED_PROJECTS),
    )
    assert rc == 0
    token = json.loads(out.read_text())
    assert token["project_id"] == 1
    assert token["resolved_title"] == "Published Data"
    assert token["confirmed"] is True
    assert token["accessible_project_ids"] == [1, 2]
    assert {"id": 1, "title": "Published Data"} in token["accessible_projects"]


def test_project_resolve_by_name_two_step_unconfirmed(tmp_path):
    # Step 1 of the curator flow: resolve the name WITHOUT --confirmed -> id+title surfaced for
    # curator confirmation, token not yet valid (rc 1, confirmed false).
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--name", "Published Data", "--out", str(out)],
        transport=StubClient(projects=_NAMED_PROJECTS),
    )
    assert rc == 1
    token = json.loads(out.read_text())
    assert token["project_id"] == 1
    assert token["resolved_title"] == "Published Data"
    assert token["confirmed"] is False


def test_project_resolve_unknown_name_fails_closed(tmp_path):
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--name", "Nope", "--confirmed", "--out", str(out)],
        transport=StubClient(projects=_NAMED_PROJECTS),
    )
    assert rc == 1
    token = json.loads(out.read_text())
    assert token["confirmed"] is False
    assert token["project_id"] is None
    assert token["error"] == "name_not_found"
    # The accessible titles are surfaced so the agent can show the curator real options.
    assert {"id": 1, "title": "Published Data"} in token["accessible_projects"]


def test_project_resolve_ambiguous_name_fails_closed(tmp_path):
    dup = [
        {"id": "1", "attributes": {"title": "Published Data"}},
        {"id": "7", "attributes": {"title": "Published Data"}},
    ]
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--name", "Published Data", "--confirmed", "--out", str(out)],
        transport=StubClient(projects=dup),
    )
    assert rc == 1
    token = json.loads(out.read_text())
    assert token["confirmed"] is False
    assert token["project_id"] is None
    assert token["error"] == "name_ambiguous"


def test_project_resolve_name_id_mismatch_fails_closed(tmp_path):
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--project-id", "2", "--name", "Published Data",
         "--confirmed", "--out", str(out)],
        transport=StubClient(projects=_NAMED_PROJECTS),
    )
    assert rc == 1
    token = json.loads(out.read_text())
    assert token["confirmed"] is False
    assert token["error"] == "name_id_mismatch"


def test_project_resolve_by_id_exposes_title(tmp_path):
    out = tmp_path / "project.json"
    rc = runner.main(
        ["project-resolve", "--project-id", "1", "--confirmed", "--out", str(out)],
        transport=StubClient(projects=_NAMED_PROJECTS),
    )
    assert rc == 0
    token = json.loads(out.read_text())
    assert token["resolved_title"] == "Published Data"
    assert token["confirmed"] is True


def test_project_resolve_requires_id_or_name(tmp_path):
    with pytest.raises(SystemExit) as exc:
        runner.main(["project-resolve", "--out", str(tmp_path / "p.json")],
                    transport=StubClient())
    assert exc.value.code == 2


def test_build_payload_staging_command(tmp_path):
    rows = _rows_file(tmp_path, [_create_row(assay_ids=[9])])
    rc = runner.main(
        [
            "build-payload",
            "--rows", str(rows),
            "--schema", str(_schema_file(tmp_path)),
            "--id-to-title", str(_titles_file(tmp_path, {9: "RNA-seq"})),
            "--out", str(tmp_path / "stage"),
        ]
    )
    assert rc == 0
    assert (tmp_path / "stage" / "payload_flat.xlsx").exists()


def test_unknown_subcommand_errors():
    assert runner.main(["definitely-not-a-command"]) != 0


def test_defensive_branches_and_helpers(tmp_path, capsys, monkeypatch):
    runner._emit_gate(runner.GateError("x", "detail"))
    assert json.loads(capsys.readouterr().out) == {"gate": "x", "detail": "detail"}

    sentinel = object()
    monkeypatch.setattr(runner.BatchUploadClient, "from_env", classmethod(lambda cls, transport=None: sentinel))
    assert runner._client("transport") is sentinel

    bad_rows = tmp_path / "bad_rows.json"
    bad_rows.write_text("{}")
    with pytest.raises(runner.GateError, match="rows"):
        runner._load_rows(bad_rows)
    with pytest.raises(runner.GateError, match="project_unconfirmed"):
        runner._read_confirmation(None, 1)
    with pytest.raises(runner.GateError, match="non_empty"):
        runner._preflight_non_empty([])
    assert runner._assay_touching(_update_row(assay_titles=["RNA-seq"]))

    with pytest.raises(runner.GateError, match="schema"):
        runner._schema_for_rows(StubClient(), [{"SampleType": ""}])
    with pytest.raises(runner.GateError, match="schema"):
        runner._schema_for_rows(StubClient(schema={"sample_type": "TIS", "attributes": []}), [_create_row()])
    with pytest.raises(runner.GateError, match="schema"):
        runner._schema_for_rows(StubClient(schema={"sample_type": "TIS", "attributes": [{"required": True}]}), [_create_row()])
    with pytest.raises(runner.GateError, match="schema"):
        runner._schema_for_rows(StubClient(schema={"sample_type": "TIS", "attributes": [{"title": "Name"}]}), [_create_row()])

    manifest = runner._resolve_manifest(
        [_update_row(assay_ids=[9])],
        StubClient(search_rows=[{"json_metadata": {"UID": UID}, "assays": TITLE, "assay_titles": [TITLE]}]),
        {TITLE: [351, 260]},
        {351, 260},
    )
    assert manifest[UID]["retrieve_status"] == "degraded"

    artifact = tmp_path / "payload_flat.xlsx"
    _write_artifact(artifact, [{"UID": "", "json_metadata": {}, "assay_ids": []}])
    with pytest.raises(runner.GateError, match="required_missing"):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={})
    _write_artifact(artifact, [{"UID": UID, "json_metadata": {"UID": UID}, "assay_ids": []}])
    with pytest.raises(runner.GateError, match="manifest"):
        runner._artifact_gate(artifact=artifact, validation=_valid(1), manifest={})

    with pytest.raises(runner.GateError, match="staging"):
        runner._promote(pathlib.Path("/data/scratch/payload_flat.xlsx"), tmp_path)
    current = _current_file(tmp_path, {UID: [351, 260]})
    assert runner._load_current(str(current)) == {UID: {351, 260}}

    bad_addition = _run(tmp_path, StubClient(), [_create_row(assay_titles=["Missing"])])
    assert bad_addition != 0
    assert _last_gate(capsys)["gate"] == "assay_resolution"


def _write_artifact(path, rows):
    records = []
    for row in rows:
        record = {
            "UID": row.get("UID", ""),
            "SampleType": row.get("SampleType", "TIS"),
            "json_metadata": orjson.dumps(row["json_metadata"]).decode(),
            "assay_ids": orjson.dumps(row.get("assay_ids", [])).decode(),
            "assay_titles": "[]",
        }
        for key, value in row.items():
            if key not in record and key not in {"json_metadata", "assay_ids"}:
                record[key] = value
        records.append(record)
    pl.DataFrame(records).write_excel(
        workbook=str(path),
        worksheet="Samples",
        include_header=True,
        autofilter=False,
        table_style=None,
    )


def test_build_payload_accepts_single_dict_schema_file(tmp_path):
    """Defect 3: nextseek-sampletype-attrs emits one dict; build-payload must accept it."""
    schema_path = tmp_path / "attrs.json"
    schema_path.write_text(json.dumps(SCHEMA[0]))
    rows = _rows_file(tmp_path, [_create_row(assay_ids=[9])])
    rc = runner.main(
        [
            "build-payload",
            "--rows", str(rows),
            "--schema", str(schema_path),
            "--id-to-title", str(_titles_file(tmp_path, {9: "RNA-seq"})),
            "--out", str(tmp_path / "stage"),
        ]
    )
    assert rc == 0
    assert (tmp_path / "stage" / "payload_flat.xlsx").exists()


def test_resolve_manifest_reuses_prefetched_rows_without_search():
    """Defect 2: when sample-search rows are already in hand, do not search again."""
    client = StubClient()
    prefetched = [
        {
            "id": 324503,
            "numeric_seek_id": 324503,
            "json_metadata": {"UID": UID},
            "assays": TITLE,
            "assay_titles": [TITLE],
        }
    ]
    manifest = runner._resolve_manifest(
        [_update_row()],
        client,
        {TITLE: [351, 260]},
        {351, 260},
        prefetched=prefetched,
    )
    assert not any(call[0] == "search" for call in client.calls)
    assert manifest[UID]["retrieve_status"] == "verified"
    assert set(manifest[UID]["current_assay_ids"]) == {351}


def test_build_payload_resolved_current_from_sample_search_no_second_search(tmp_path):
    """Defect 2: --resolved-current may be sample-search list; resolve without re-search."""
    client = StubClient()
    search_path = tmp_path / "sample_search.json"
    search_path.write_text(
        json.dumps(
            [
                {
                    "id": 324503,
                    "numeric_seek_id": 324503,
                    "json_metadata": {"UID": UID},
                    "assays": TITLE,
                    "assay_titles": [TITLE],
                }
            ]
        )
    )
    rows = _rows_file(tmp_path, [_update_row(attributes={"Treatment": "drug"})])
    rc = runner.main(
        [
            "build-payload",
            "--rows", str(rows),
            "--schema", str(_schema_file(tmp_path)),
            "--resolved-current", str(search_path),
            "--project-id", "1",
            "--id-to-title", str(_titles_file(tmp_path)),
            "--out", str(tmp_path / "stage"),
        ],
        transport=client,
    )
    assert rc == 0, client.calls
    assert not any(call[0] == "search" for call in client.calls)
    assert (tmp_path / "stage" / "payload_flat.xlsx").exists()


def test_runner_error_includes_message_and_stderr_traceback(tmp_path, capsys, monkeypatch):
    """Defect 4: runner_error must carry the exception message; stderr gets a traceback."""

    def boom(_argv, *, transport=None):
        raise RuntimeError("distinct-failure-message")

    monkeypatch.setitem(runner._CMDS, "build-payload", boom)
    rc = runner.main(["build-payload"])
    captured = capsys.readouterr()
    assert rc != 0
    gate = json.loads(captured.out.strip().splitlines()[-1])
    assert gate["gate"] == "runner_error"
    assert "RuntimeError" in gate["detail"]
    assert "distinct-failure-message" in gate["detail"]
    assert "Traceback" in captured.err
