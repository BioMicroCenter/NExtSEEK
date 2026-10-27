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
    assert service.unfinished_run_for("10.1000/xyz123", "38000001") is None
    live = service.create_run(user=user, paper_input="38000001", paper=_paper())
    assert service.unfinished_run_for("10.1000/xyz123", "38000001") == live
    assert service.unfinished_run_for("10.1000/XYZ123", "PMC1") == live
    assert service.unfinished_run_for(None, "38000001") is None  # keyed by the DOI the run has


@pytest.mark.django_db
def test_unfinished_run_for_skips_superseded_runs(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator")
    # The live run is the OLDEST row; the excluded rows are newer, so ordering cannot hide a missing filter.
    live = service.create_run(user=user, paper_input="38000001", paper=_paper(doi=""))
    service.set_state(live, "failed")  # free the paper while the newer rows are made
    by_state = service.create_run(user=user, paper_input="38000001", paper=_paper(doi=""))
    service.set_state(by_state, "superseded", reason="replaced")
    by_link = service.create_run(user=user, paper_input="38000001", paper=_paper(doi=""))
    by_link.superseded_by = live
    by_link.save(update_fields=["superseded_by"])
    assert by_link.state == "queued"
    service.set_state(live, "queued")
    assert service.unfinished_run_for(None, "38000001") == live


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
    assert service.unfinished_run_for(doi, "38000002") is None
    assert PaperReingestRun.objects.get(pk=by_link.pk).live_key is None


@pytest.mark.django_db
def test_a_second_user_cannot_start_a_paper_another_user_is_running(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    first = django_user_model.objects.create(username="curator")
    second = django_user_model.objects.create(username="someone")
    live = service.create_run(user=first, paper_input="38000001", paper=_paper())
    assert service.unfinished_run_for("10.1000/xyz123", "https://doi.org/10.1000/xyz123") == live
    with pytest.raises(service.RunAlreadyLive) as err:
        service.create_run(user=second, paper_input="https://doi.org/10.1000/xyz123", paper=_paper(doi="10.1000/XYZ123"))
    assert err.value.run == live
    assert PaperReingestRun.objects.count() == 1
    # The loser's paper.json is not left behind with no row.
    assert sorted(p.name for p in tmp_path.iterdir()) == [live.run_id]
    # Once the first run ends, the paper is free again.
    service.set_state(live, "failed", failed_stage="cancelled", reason="cancelled by operator")
    again = service.create_run(user=second, paper_input="38000001", paper=_paper())
    assert service.unfinished_run_for("10.1000/xyz123", "38000001") == again


@pytest.mark.django_db
def test_the_database_itself_refuses_a_second_live_row(django_user_model, tmp_path, monkeypatch):
    """The read-then-create check can race; the unique live_key cannot."""
    from django.db import IntegrityError, transaction

    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator")
    service.create_run(user=user, paper_input="38000001", paper=_paper())
    with pytest.raises(IntegrityError), transaction.atomic():
        PaperReingestRun.objects.create(user=user, paper_input="38000001", doi="10.1000/xyz123")
    finished = PaperReingestRun.objects.create(user=user, paper_input="38000001", doi="10.1000/xyz123",
                                               state="failed")
    assert finished.live_key is None


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
    assert service.unfinished_run_for("10.1000/xyz123", "38000001") is None


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
    assert cur.params == ["10.1000/xyz123", "doi:10.1000/xyz123", "https://doi.org/10.1000/xyz123",
                          "http://doi.org/10.1000/xyz123", "https://dx.doi.org/10.1000/xyz123",
                          "http://dx.doi.org/10.1000/xyz123"]
    assert "JSON_EXTRACT" in cur.sql and "projects_samples" in cur.sql
    assert "LOWER(TRIM(" in cur.sql  # the stored value is case folded, so DOI: and HTTPS://DX.DOI.ORG/ match too


@pytest.mark.parametrize("given", ["doi:10.1000/xyz123", "DOI: 10.1000/xyz123", "https://dx.doi.org/10.1000/XYZ123",
                                   "http://doi.org/10.1000/xyz123", " 10.1000/xyz123 "])
def test_doi_lookup_takes_a_prefixed_doi_as_its_bare_form(monkeypatch, settings, given):
    cur = _Cursor()
    monkeypatch.setattr("django.db.connections", {settings.SEEK_DATABASE: _Conn(cur)})
    assert lookups.doi_project_ids_strict(given) == []
    assert cur.params[0] == "10.1000/xyz123"


def test_doi_prefix_forms_match_like_mysql_would():
    """The SQL compares LOWER(TRIM(stored)) with the forms; mirror that here over stored values seen in SEEK."""
    forms = set(lookups.doi_forms("10.1000/xyz123"))
    for stored in ("10.1000/xyz123", "DOI:10.1000/XYZ123", "https://doi.org/10.1000/xyz123",
                   "http://doi.org/10.1000/xyz123", "HTTPS://DX.DOI.ORG/10.1000/xyz123",
                   "http://dx.doi.org/10.1000/xyz123 "):
        assert stored.strip().lower() in forms, stored
    assert "https://doi.org/10.1000/xyz1234" not in forms


def test_doi_lookup_outage_raises(monkeypatch, settings):
    monkeypatch.setattr("django.db.connections", {settings.SEEK_DATABASE: _Conn(_Cursor(fail=True))})
    with pytest.raises(RuntimeError):
        lookups.doi_project_ids_strict("10.1000/xyz123")
