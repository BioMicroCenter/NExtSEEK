"""#139: reingest a run Nessie did not launch (ruling G-1, amended 2026-11-12).

A run folder under the runs root with no PipelineRun is harvested for a superuser only.
Nessie reads the run's identity from the run itself: the pipeline and release from its
own pipeline_info, a committed map for that pipeline, and its samplesheet, the only
source of the samples' parents. Such a manifest is not test-only, so its builds can be
uploaded; it never makes a PipelineRun, and its folder gets no saved.txt.
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import build_records, harvest, upload
from NessieAI.ns.reingest import manifest as manifest_mod
from NessieAI.ns.reingest import store as store_mod
from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

RUN_DIR = "/net/cluster/runs/aws_sarek"
ADMIN = SimpleNamespace(user=SimpleNamespace(is_superuser=True, pk=3))
CURATOR = SimpleNamespace(user=SimpleNamespace(is_superuser=False, pk=7))
SAREK_VERSIONS = "pipeline_info/nf_core_sarek_software_mqc_versions.yml"


class _Cfg:
    LURIA_ENV = {"working_path": "/net/cluster", "key": "/dev/null",
                 "user": "user", "host": "cluster.example.edu"}


def _harvested(*, name="nf-core/sarek", version="3.10.0", sources=None):
    if sources is None:
        sources = {"software_versions": SAREK_VERSIONS, "samples": "samplesheet.csv",
                   "samplesheet": "samplesheet.csv"}
    return manifest_mod.RunManifest(
        run_dir=RUN_DIR, pipeline=manifest_mod.PipelineInfo(name=name, version=version),
        execution=manifest_mod.ExecutionInfo(processes=3),
        samples=[manifest_mod.SampleRecord(
            nfcore_sample="NA12878", d_seq_uid="D.SEQ-X-1",
            uid_resolution=manifest_mod.RESOLUTION_FASTQ_EXACT)],
        sources=sources)


@pytest.fixture
def staged(monkeypatch, tmp_path):
    """run-harvest with staging and parsing replaced by a manifest the test picks."""
    state = {"manifest": _harvested(), "staged": False}

    def fake_stage(*a, **k):
        state["staged"] = True
        return [], []

    monkeypatch.setattr(g, "_stage_run_dir", fake_stage)
    monkeypatch.setattr(harvest, "harvest_local", lambda root, **k: state["manifest"])
    monkeypatch.setattr(ssh, "prepare_key", lambda k: "/tmp/key")
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    return state


def _harvest(session):
    return g.run_op("run-harvest", {"run_dir": RUN_DIR}, config=_Cfg(), session=session,
                    write_gate=None, neo4j_exec=None, outputs_dir=None)


def test_a_non_admin_is_refused_before_anything_is_staged(staged):
    with pytest.raises(g.OpValidationError, match="only an admin") as err:
        _harvest(CURATOR)
    assert err.value.error_type == "foreign_run_needs_an_admin"
    assert staged["staged"] is False


def test_an_admin_harvests_it_as_a_foreign_run_that_is_not_test_only(staged):
    out = _harvest(ADMIN)
    loaded = store_mod.load_manifest(out["manifest_id"])
    assert loaded.launch_record_id is None
    assert loaded.foreign_run is not None
    assert loaded.foreign_run.harvested_by_user_id == 3
    assert loaded.foreign_run.pipeline_source == SAREK_VERSIONS
    assert loaded.foreign_run.samplesheet_source == "samplesheet.csv"
    assert loaded.test_only is False
    # Read-only: no launch record is made for it.
    assert not PipelineRun.objects.filter(run_dir=RUN_DIR).exists()


@pytest.mark.parametrize("name, version, sources", [
    ("", "", {"samplesheet": "samplesheet.csv"}),
    ("nf-core/sarek", "", {"software_versions": SAREK_VERSIONS, "samplesheet": "samplesheet.csv"}),
    ("nf-core/sarek", "3.10.0", {"samplesheet": "samplesheet.csv"}),
])
def test_a_run_whose_files_name_no_pipeline_and_release_is_refused(staged, name, version, sources):
    staged["manifest"] = _harvested(name=name, version=version, sources=sources)
    with pytest.raises(g.OpValidationError) as err:
        _harvest(ADMIN)
    assert err.value.error_type == "foreign_run_names_no_pipeline"


def test_a_pipeline_with_no_map_is_refused(staged):
    staged["manifest"] = _harvested(name="nf-core/nosuchpipeline")
    with pytest.raises(g.OpValidationError) as err:
        _harvest(ADMIN)
    assert err.value.error_type == "foreign_run_has_no_map"


def test_a_run_with_no_samplesheet_is_refused(staged):
    """Sample names read off MultiQC carry no reads, so no parent can be matched."""
    staged["manifest"] = _harvested(sources={
        "software_versions": SAREK_VERSIONS,
        "samples": "multiqc/multiqc_data/multiqc_general_stats.txt"})
    with pytest.raises(g.OpValidationError) as err:
        _harvest(ADMIN)
    assert err.value.error_type == "foreign_run_has_no_samplesheet"


def test_a_launched_run_is_still_taken_on_its_launch_record(staged):
    run = PipelineRun.objects.create(run_dir=RUN_DIR, run_name="a", pipeline="nf-core/sarek",
                                     revision="3.10.0", launched_by_id=None)
    out = _harvest(ADMIN)
    loaded = store_mod.load_manifest(out["manifest_id"])
    assert loaded.launch_record_id == run.pk
    assert loaded.foreign_run is None


def test_a_local_harvest_is_still_test_only(tmp_path):
    got = harvest.harvest_local(str(tmp_path))
    assert got.foreign_run is None
    assert got.test_only is True


def test_the_harvest_names_the_samplesheet_it_read(tmp_path):
    (tmp_path / "samplesheet.csv").write_text(
        "patient,status,sample,lane,fastq_1,fastq_2\nP,0,S1,1,/r/S1_1.fq.gz,/r/S1_2.fq.gz\n")
    got = harvest.harvest_local(str(tmp_path))
    assert got.sources["samplesheet"] == "samplesheet.csv"


# --- the upload --------------------------------------------------------------------------

@pytest.fixture
def builds(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    return tmp_path


def _record(root, *, test_only, origin):
    path = root / f"reingest_A_ALN_{origin}.xlsx"
    path.write_bytes(origin.encode())
    return build_records.write(
        path=str(path), artifact_key=f"reingest_A_ALN_{origin}", sample_type="A.ALN",
        mode="new", manifest_id="m1", disposition="CLEAN", open_warnings=[], row_count=1,
        project_id=1, project_note="", answers_digest="d", user_id=3, test_only=test_only,
        chat_turn={"session_id": "chat-1", "turn_number": 1, "task_id": "t1"}, origin=origin)


def test_a_build_record_names_its_origin(builds):
    assert _record(builds, test_only=False, origin="foreign")["origin"] == "foreign"
    path = builds / "plain.xlsx"
    path.write_bytes(b"plain")
    plain = build_records.write(
        path=str(path), artifact_key="plain", sample_type="A.ALN", mode="new",
        manifest_id="m1", disposition="CLEAN", open_warnings=[], row_count=1, project_id=1,
        project_note="", answers_digest="d", user_id=3, test_only=True)
    assert plain["origin"] == "test"


def test_verify_takes_a_foreign_build_and_still_refuses_a_test_only_one(builds):
    foreign = _record(builds, test_only=False, origin="foreign")
    test = _record(builds, test_only=True, origin="test")
    with pytest.raises(upload.UploadRefused) as refused:
        upload.verify([foreign["build_id"], test["build_id"]], user_id=3)
    reasons = " ".join(refused.value.reasons)
    assert "test only" in reasons
    assert foreign["artifact_key"] + ":" not in reasons


def test_no_saved_label_is_written_into_a_foreign_run_folder(monkeypatch):
    from NessieAI.ns.reingest import launch_record

    manifests = {"m1": types.SimpleNamespace(run_dir="/w/runs/aws_sarek", launch_record_id=None,
                                             foreign_run=object())}
    monkeypatch.setattr(store_mod, "load_manifest", lambda mid: manifests[mid])
    monkeypatch.setattr(launch_record, "mark_reingested", lambda pk: None)
    written = []
    monkeypatch.setattr(g, "_write_saved_label", lambda config, manifest: written.append(manifest))
    g._mark_runs_reingested([{"manifest_id": "m1", "job_id": "j1",
                              "outcome": {"status": "succeeded"}}], None)
    assert written == []


def test_the_build_header_says_how_each_sample_was_matched():
    run_manifest = _harvested()
    run_manifest.samples.append(manifest_mod.SampleRecord(
        nfcore_sample="NA12891", d_seq_uid="D.SEQ-X-2",
        uid_resolution=manifest_mod.RESOLUTION_FASTQ_BASENAME))
    run_manifest.samples.append(manifest_mod.SampleRecord(
        nfcore_sample="NA12892", uid_resolution=manifest_mod.RESOLUTION_AMBIGUOUS))
    run_manifest.foreign_run = manifest_mod.ForeignRun(
        harvested_by_user_id=3, harvested_at="2026-11-12T00:00:00+00:00",
        pipeline_source=SAREK_VERSIONS, samplesheet_source="samplesheet.csv")
    header = g._foreign_run_header(run_manifest)
    assert header.startswith("NOT LAUNCHED BY NESSIE: nf-core/sarek 3.10.0")
    assert "- NA12878: D.SEQ-X-1 (by the full read path)" in header
    assert "- NA12891: D.SEQ-X-2 (by the read file name only)" in header
    assert "- NA12892: no parent found (ambiguous)" in header


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs"


def test_nf_cores_own_aws_results_are_taken_on_their_own_files():
    """nf-core's published AWS results of sarek 3.10.0, a run Nessie never launched: its
    pipeline_info names the pipeline and release, and its samplesheet names the reads."""
    folder = FIXTURES / "sarek" / "3.10.0-aws-test_full_germline_ncbench_agilent"
    run = harvest.harvest_local(str(folder), run_dir="/RUN",
                                inventory=json.loads((folder / "inventory.json").read_text()))
    evidence = g._foreign_run_evidence(run, ADMIN.user)
    assert run.pipeline.name == "nf-core/sarek"
    assert evidence.pipeline_source.startswith("pipeline_info/")
    assert evidence.samplesheet_source == "samplesheet.csv"


def test_a_folder_with_only_execution_traces_is_refused(tmp_path):
    """nf-core/atacseq 2.1.2's AWS results keep only execution traces and reports: nothing
    in them names the pipeline, so the run cannot be told from an arbitrary folder."""
    (tmp_path / "pipeline_info").mkdir()
    (tmp_path / "pipeline_info" / "execution_trace_2023-08-07_22-04-16.txt").write_text(
        "task_id\thash\tnative_id\tname\tstatus\texit\n1\tab/cd\t1\tFASTQC (1)\tCOMPLETED\t0\n")
    (tmp_path / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,replicate\nS,/r/S_1.fq.gz,/r/S_2.fq.gz,1\n")
    run = harvest.harvest_local(str(tmp_path), run_dir="/RUN")
    with pytest.raises(g.OpValidationError) as err:
        g._foreign_run_evidence(run, ADMIN.user)
    assert err.value.error_type == "foreign_run_names_no_pipeline"
