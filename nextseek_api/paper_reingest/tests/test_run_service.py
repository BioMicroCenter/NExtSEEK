import pytest

from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
from NessieAI.ns.paper.store import load_artifact
from nextseek_api.paper_reingest import lookups, service
from nextseek_api.paper_reingest.models_db import PaperReingestRun


def _paper(doi="10.1000/xyz123", mode="new"):
    return PaperArtifact(ref=PaperRef(pmid="38000001", doi=doi, title="Mice and RNA."), text=PaperText(),
                         text_source="europepmc", mode=mode)


@pytest.mark.django_db
def test_create_run_saves_paper_artifact(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator", is_superuser=True)
    run = service.create_run(user=user, paper_input="38000001", paper=_paper(), project_hint=4, chat_session_id="s1")
    run.refresh_from_db()
    assert (run.state, run.mode, run.doi, run.pmid, run.project_hint) == ("queued", "new", "10.1000/xyz123", "38000001", 4)
    assert len(run.run_id) == 32
    assert load_artifact(run.run_id, run.artifacts["paper"], PaperArtifact).ref.title == "Mice and RNA."


@pytest.mark.django_db
def test_unfinished_run_for_ignores_finished_and_superseded(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator")
    old = service.create_run(user=user, paper_input="38000001", paper=_paper())
    service.set_state(old, "failed", failed_stage="acquire", reason="boom")
    assert service.unfinished_run_for(user, "10.1000/xyz123", "38000001") is None
    live = service.create_run(user=user, paper_input="38000001", paper=_paper())
    assert service.unfinished_run_for(user, "10.1000/xyz123", "38000001") == live
    assert service.unfinished_run_for(user, None, "38000001") == live
    other = django_user_model.objects.create(username="someone")
    assert service.unfinished_run_for(other, "10.1000/xyz123", "38000001") is None


@pytest.mark.django_db
def test_unfinished_run_for_skips_superseded_runs(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator")
    # The live run is the OLDEST row; the excluded rows are newer, so ordering cannot hide a missing filter.
    live = service.create_run(user=user, paper_input="38000001", paper=_paper())
    by_state = service.create_run(user=user, paper_input="38000001", paper=_paper())
    service.set_state(by_state, "superseded", reason="replaced")
    by_link = service.create_run(user=user, paper_input="38000001", paper=_paper())
    by_link.superseded_by = live
    by_link.save(update_fields=["superseded_by"])
    assert by_link.state == "queued"
    assert service.unfinished_run_for(user, "10.1000/xyz123", "38000001") == live
    assert service.unfinished_run_for(user, None, "38000001") == live


@pytest.mark.django_db
def test_unfinished_run_for_is_none_when_only_superseded_runs_exist(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="other-curator")
    doi = "10.2000/only-superseded"
    target = service.create_run(user=user, paper_input="38000002", paper=_paper(doi=doi))
    service.set_state(target, "failed", failed_stage="acquire", reason="boom")
    by_state = service.create_run(user=user, paper_input="38000002", paper=_paper(doi=doi))
    service.set_state(by_state, "superseded", reason="replaced")
    by_link = service.create_run(user=user, paper_input="38000002", paper=_paper(doi=doi))
    by_link.superseded_by = target
    by_link.save(update_fields=["superseded_by"])
    assert service.unfinished_run_for(user, doi, "38000002") is None
    assert service.unfinished_run_for(user, None, "38000002") is None


@pytest.mark.django_db
def test_create_run_leaves_no_row_when_artifact_write_fails(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("nextseek_api.paper_reingest.service.save_artifact", boom)
    before = PaperReingestRun.objects.count()
    with pytest.raises(OSError):
        service.create_run(user=user, paper_input="38000001", paper=_paper())
    assert PaperReingestRun.objects.count() == before
    assert service.unfinished_run_for(user, "10.1000/xyz123", "38000001") is None


@pytest.mark.django_db
def test_set_state_records_reason(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="x", paper=_paper())
    service.set_state(run, "paused", reason="waiting for www.ebi.ac.uk: HTTP 503")
    assert PaperReingestRun.objects.get(pk=run.pk).reason.startswith("waiting for")


@pytest.mark.django_db
def test_set_state_rejects_unknown_state(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="x", paper=_paper())
    with pytest.raises(ValueError):
        service.set_state(run, "aquiring")
    assert PaperReingestRun.objects.get(pk=run.pk).state == "queued"


class _Cursor:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.sql, self.params = rows or [], fail, None, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.fail:
            raise OSError("down")
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_doi_lookup_matches_bare_and_url_forms(monkeypatch, settings):
    cur = _Cursor(rows=[(7,), (9,)])
    monkeypatch.setattr("django.db.connections", {settings.SEEK_DATABASE: _Conn(cur)})
    assert lookups.doi_project_ids_strict("10.1000/XYZ123") == [7, 9]
    assert cur.params == ["10.1000/xyz123", "https://doi.org/10.1000/xyz123"]
    assert "JSON_EXTRACT" in cur.sql and "projects_samples" in cur.sql


def test_doi_lookup_outage_raises(monkeypatch, settings):
    monkeypatch.setattr("django.db.connections", {settings.SEEK_DATABASE: _Conn(_Cursor(fail=True))})
    with pytest.raises(RuntimeError):
        lookups.doi_project_ids_strict("10.1000/xyz123")
