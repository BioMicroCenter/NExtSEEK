import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from NessieAI.cc.spawner_client import SpawnerUnavailable
from NessieAI.ns.paper.curation_models import (ContextArtifact, CurationArtifact, ExportSummary, ProjectChoice,
                                               RulebookVersion, SampleTypeDef, VerifiedArtifact)
from NessieAI.ns.paper.models import (EvidenceArtifact, EvidenceItem, PaperArtifact, PaperRef, PaperText,
                                      SkeletonRow)
from NessieAI.ns.paper.store import load_artifact, save_artifact
from nextseek_api.paper_reingest import launcher, service
from nextseek_api.paper_reingest.models_db import PaperReingestRun

REF = PaperRef(pmid="1", doi="10.1/x", published="2024-05-20")
EVIDENCE = EvidenceArtifact(
    paper=REF, deposits=[], gaps=[],
    items=[EvidenceItem(id="E:methods:p1", text="All mice were female.", source="Methods p1")],
    skeleton=[SkeletonRow(row_key="geo:GSM1", repo="geo", record_id="GSM1", kind="raw", submitted=date(2024, 2, 3))])
CONTEXT = ContextArtifact(
    mode="new", project=ProjectChoice(),
    sample_types=[SampleTypeDef(code="MUS", attributes=[{"title": "Sex"}]),
                  SampleTypeDef(code="D.SEQ", parents=["MUS"])],
    house_assays=["RNA-seq"], lab_code="MIT",
    export=ExportSummary(project_ids=[], pulled_at=datetime.now(timezone.utc), samples=0, uids=[]),
    rulebook=RulebookVersion(file_sha256="0" * 64))
GOOD = {"tree": [{"node_id": "mus", "sample_type": "MUS", "label": "mice"},
                 {"node_id": "seq", "sample_type": "D.SEQ", "label": "seq", "skeleton_rows": ["geo:GSM1"],
                  "parents": [{"node_id": "mus", "assay": "RNA-seq"}]}],
        "rows": [{"row_key": "node:mus:1", "node_id": "mus",
                  "values": {"Sex": {"value": "female", "evidence": ["E:methods:p1"]}}},
                 {"row_key": "geo:GSM1", "node_id": "seq"}]}
BAD = json.loads(json.dumps(GOOD))
BAD["rows"][0]["values"]["Sex"]["value"] = "male"
SETTINGS = launcher.LaunchSettings(timeout_s=60, max_turns=200, budget_usd=5.0)


class FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    def read_event_line(self):
        return self._lines.pop(0) if self._lines else None

    def close(self):
        pass


class FakeSpawner:
    """Writes the scripted curation.json into the launch's scratch folder, as the agent would."""

    def __init__(self, users_root: Path, outputs, *, down=False):
        self.users_root, self.outputs, self.down = users_root, list(outputs), down
        self.bodies, self.stopped = [], []

    def start_turn(self, body, *, read_timeout):
        if self.down:
            raise SpawnerUnavailable("cc-spawner unreachable: ConnectionError")
        self.bodies.append(body)
        out = self.outputs.pop(0)
        scratch = self.users_root / body["project_dirname"] / body["user_id"] / "scratch" / body["run_id"]
        if out is not None:
            (scratch / "curation.json").write_text(out if isinstance(out, str) else json.dumps(out))
        return FakeStream([json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 1.25})])

    def stop_turn(self, run_id):
        self.stopped.append(run_id)
        return True


@pytest.fixture
def run(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(launcher, "_model_ids", lambda: ("model-a", None))
    paper = PaperArtifact(ref=REF, text=PaperText(), text_source="europepmc", mode="new")
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="1", paper=paper)
    keys = {**run.artifacts, "evidence": save_artifact(run.run_id, "evidence", EVIDENCE),
            "context": save_artifact(run.run_id, "context", CONTEXT)}
    service.transition(run, ("queued",), "curation_pending", artifacts=keys)
    return run


def _row(run):
    return PaperReingestRun.objects.get(pk=run.pk)


@pytest.mark.django_db
def test_a_clean_curation_goes_to_building_with_both_artifacts(run, tmp_path):
    spawner = FakeSpawner(tmp_path / "users", [GOOD])
    assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users") == "building"
    row = _row(run)
    assert row.state == "building"
    verified = load_artifact(run.run_id, row.artifacts["verified"], VerifiedArtifact)
    assert verified.held == [] and not verified.bounced and len(verified.uid_plan) == 2
    assert load_artifact(run.run_id, row.artifacts["curation"], CurationArtifact).rows[0].row_key == "node:mus:1"
    body = spawner.bodies[0]
    assert (body["project_dirname"], body["user_id"], body["max_turns"]) == ("paper-reingest", run.run_id, 200)
    assert "NEXTSEEK_TURN_PASS" not in body["environment"] and "NEXTSEEK_USERNAME" not in body["environment"]
    staged = tmp_path / "users" / "paper-reingest" / run.run_id / "input"
    assert {p.name for p in staged.iterdir()} == {"paper.json", "evidence.json", "context.json", "rulebook.json"}


@pytest.mark.django_db
def test_errors_bounce_once_with_the_findings_and_what_still_fails_is_blanked(run, tmp_path):
    spawner = FakeSpawner(tmp_path / "users", [BAD, BAD])
    assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users") == "building"
    assert len(spawner.bodies) == 2 and "second and last attempt" in json.loads(
        spawner.bodies[1]["stdin_envelope"])["message"]["content"]
    staged = tmp_path / "users" / "paper-reingest" / run.run_id / "input"
    assert (staged / "previous_curation.json").exists() and (staged / "findings.json").exists()
    verified = load_artifact(run.run_id, _row(run).artifacts["verified"], VerifiedArtifact)
    assert verified.bounced and verified.curation.rows[0].values["Sex"].blank == "not_in_sources"


@pytest.mark.django_db
def test_no_output_twice_fails_the_run_at_curate(run, tmp_path):
    spawner = FakeSpawner(tmp_path / "users", [None, "{not json"])
    assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users") == "failed"
    assert (_row(run).state, _row(run).failed_stage) == ("failed", "curate")


@pytest.mark.django_db
def test_a_structural_problem_fails_the_run_at_verify(run, tmp_path):
    broken = json.loads(json.dumps(GOOD))
    broken["rows"][1]["node_id"] = "ghost"
    spawner = FakeSpawner(tmp_path / "users", [broken, broken])
    assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users") == "failed"
    assert _row(run).failed_stage == "verify"


@pytest.mark.django_db
def test_a_spawner_outage_puts_the_run_back_until_the_attempts_run_out(run, tmp_path):
    spawner = FakeSpawner(tmp_path / "users", [], down=True)
    for _ in range(launcher.MAX_LAUNCH_ATTEMPTS - 1):
        assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS,
                               users_root=tmp_path / "users") == "curation_pending"
    assert launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users") == "failed"
    assert _row(run).failed_stage == "curate" and "unreachable" in _row(run).reason


@pytest.mark.django_db
def test_a_run_not_pending_is_skipped(run, tmp_path):
    service.transition(run, ("curation_pending",), "failed", failed_stage="cancelled")
    assert launcher.curate(run.run_id, client=FakeSpawner(tmp_path / "users", []), settings=SETTINGS,
                           users_root=tmp_path / "users") == "skipped:failed"


@pytest.mark.django_db
def test_a_stop_mid_run_stops_the_container_and_puts_the_run_back(run, tmp_path):
    class Stopping(FakeSpawner):
        def start_turn(self, body, *, read_timeout):
            self.bodies.append(body)
            raise launcher.LoopStopped()

    spawner = Stopping(tmp_path / "users", [])
    with pytest.raises(launcher.LoopStopped):
        launcher.curate(run.run_id, client=spawner, settings=SETTINGS, users_root=tmp_path / "users")
    assert spawner.stopped == [launcher.launch_id(run.run_id, 1)] and _row(run).state == "curation_pending"


def test_launch_ids_fit_the_spawners_run_id_pattern():
    import re
    assert re.fullmatch(r"[0-9a-f][0-9a-f-]{0,63}", launcher.launch_id("0123456789abcdef0123456789abcdef", 2))


def test_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("PAPER_REINGEST_CURATION_TIMEOUT_S", "900")
    monkeypatch.setenv("PAPER_REINGEST_CURATION_MAX_TURNS", "150")
    monkeypatch.setenv("PAPER_REINGEST_CURATION_BUDGET_USD", "7.5")
    assert launcher.LaunchSettings.from_env() == launcher.LaunchSettings(timeout_s=900, max_turns=150, budget_usd=7.5)
    monkeypatch.setenv("PAPER_REINGEST_CURATION_BUDGET_USD", "500")
    assert launcher.LaunchSettings.from_env().budget_usd == 100.0  # the spawner's own cap
