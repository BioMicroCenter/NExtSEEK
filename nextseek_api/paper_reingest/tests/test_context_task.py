from datetime import datetime, timezone

import pytest

from NessieAI.ns.paper.context import ContextSeams, ProjectFacts
from NessieAI.ns.paper.curation_models import ContextArtifact, SampleTypeDef
from NessieAI.ns.paper.models import Author, EvidenceArtifact, PaperArtifact, PaperRef, PaperText
from NessieAI.ns.paper.store import load_artifact, save_artifact
from nextseek_api.paper_reingest import context_sources, service, tasks
from nextseek_api.paper_reingest.models_db import LIVE_STATES, PaperReingestRun

REF = PaperRef(pmid="1", doi="10.1/x", authors=[Author(name="White FM")])


@pytest.fixture
def run(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    paper = PaperArtifact(ref=REF, text=PaperText(), text_source="europepmc", mode="new")
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="1", paper=paper)
    evidence = EvidenceArtifact(paper=REF, items=[], deposits=[], skeleton=[], gaps=[])
    key = save_artifact(run.run_id, "evidence", evidence)
    service.transition(run, ("queued",), "context", artifacts={**run.artifacts, "evidence": key})
    return run


def _seams(**over):
    base = dict(projects=lambda: [ProjectFacts(project_id=10, title="CSBC", pi_text="White, Forest M.")],
                sample_types=lambda: [SampleTypeDef(code="MUS")], house_assays=lambda: ["RNA-seq"],
                export_rows=lambda ids: [], precedent=lambda pid: [],
                now=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc), rulebook_sha256="a" * 64)
    base.update(over)
    return ContextSeams(**base)


def _row(run):
    return PaperReingestRun.objects.get(pk=run.pk)


def test_the_new_states_are_live():
    assert {"contextualizing", "curation_pending"} <= set(LIVE_STATES)
    assert PaperReingestRun._meta.get_field("curation_attempts").default == 0


@pytest.mark.django_db
def test_stage_2_saves_context_and_waits_for_the_curation_loop(run):
    assert tasks.run_context(run.run_id, retries_done=0, max_retries=3, seams_factory=_seams) == "curation_pending"
    row = _row(run)
    assert row.state == "curation_pending" and row.live_key is not None
    ctx = load_artifact(run.run_id, row.artifacts["context"], ContextArtifact)
    assert ctx.project.chosen.project_id == 10


@pytest.mark.django_db
def test_a_source_outage_retries_then_fails(run):
    def down():
        raise context_sources.ContextSourceDown("seek database unreachable")

    assert tasks.run_context(run.run_id, retries_done=0, max_retries=2,
                             seams_factory=lambda: _seams(projects=down)) == "context"
    assert _row(run).state == "context" and "unreachable" in _row(run).reason
    assert tasks.run_context(run.run_id, retries_done=2, max_retries=2,
                             seams_factory=lambda: _seams(projects=down)) == "failed"
    assert (_row(run).state, _row(run).failed_stage) == ("failed", "context")


@pytest.mark.django_db
def test_a_run_not_in_context_is_skipped(run):
    service.transition(run, ("context",), "failed", failed_stage="cancelled")
    assert tasks.run_context(run.run_id, retries_done=0, max_retries=3, seams_factory=_seams) == "skipped:failed"


@pytest.mark.django_db
def test_stage_1_chains_into_stage_2(run, monkeypatch):
    queued = []
    monkeypatch.setattr(service, "enqueue_context", queued.append)
    tasks.after_acquire(run.run_id, "context")
    tasks.after_acquire(run.run_id, "paused")
    assert queued == [run.run_id]


def test_the_seams_turn_a_database_error_into_source_down(monkeypatch):
    def broken():
        raise OSError("connection refused")

    monkeypatch.setattr(context_sources, "project_facts_strict", broken)
    with pytest.raises(context_sources.ContextSourceDown):
        context_sources.context_seams().projects()


def test_sample_type_defs_carry_parents_and_attributes(monkeypatch):
    from nextseek_api.services.context_catalog import SampleTypeContextEntry

    monkeypatch.setattr(context_sources, "load_sample_types_strict", lambda: [
        SampleTypeContextEntry(code="TIS", sample_type_id=3, name="Tissue", clade="Processed",
                               parent_types=["PAV", "MUS"])])
    monkeypatch.setattr(context_sources, "attributes_for_strict", lambda code: [
        {"title": "Organ", "required": True, "server_required": False}])
    (tis,) = context_sources.sample_type_defs_strict()
    assert (tis.code, tis.parents, tis.attributes[0].title, tis.attributes[0].required) == (
        "TIS", ["PAV", "MUS"], "Organ", True)
