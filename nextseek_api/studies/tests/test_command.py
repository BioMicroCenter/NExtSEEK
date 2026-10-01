"""manage.py studies (tool spec 4.5, 4.6): modes, flags, exit codes, and the credential's path."""
import base64
import contextlib
import io
import json
import logging
import os
import sys
from unittest import mock

import orjson
import pytest
from django.core.management import CommandError, call_command

from nextseek_api.management.commands import studies as cmd
from nextseek_api.studies import apply as a
from nextseek_api.studies import planner
from nextseek_api.studies.models import AssociationSet, StudyTarget
from nextseek_api.studies.tests.conftest import FakeDriver, FakeReader

PASSWORD = "Pw-4bd1 !:secreté end"


class RecordingClient:
    def __init__(self, world):
        self.world, self.timeout_s, self.auth = world, 20, []

    def _ok(self, request, body):
        self.auth.append(request.META.get("HTTP_AUTHORIZATION"))
        return orjson.dumps(body), 200, {}, None

    def get_assay(self, request, assay_id):
        return self._ok(request, self.world.assay_reps[int(assay_id)])

    def get_study(self, request, study_id):
        return self._ok(request, self.world.study_reps[int(study_id)])

    def get_current_person(self, request):
        return self._ok(request, {"data": {"id": "42", "type": "people"}})


class SessionReader(FakeReader):
    """FakeReader whose SEEK GETs go through the command's own session, so the credential's path is exercised."""

    def __init__(self, world, session):
        super().__init__(world)
        self.session = session

    def assay_representation(self, assay_id):
        return self.session.get_assay(assay_id)

    def study_representation(self, study_id):
        return self.session.get_study(study_id)


@pytest.fixture
def wired(alpha, monkeypatch, tmp_path, settings):
    settings.LOG_DIR = str(tmp_path / "logs")
    client = RecordingClient(alpha)
    monkeypatch.setattr(cmd, "_client_factory", lambda: client)
    monkeypatch.setattr(cmd, "_reader", lambda session, driver, db: SessionReader(alpha, session))
    monkeypatch.setattr(cmd, "_open_driver", lambda config: contextlib.nullcontext(FakeDriver({})))
    aset = AssociationSet(source="sheet", source_ref="s", created_at="t", targets=[
        StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One", doi="10.0000/one",
                    sample_ids=[3])])
    path = tmp_path / "associations.json"
    path.write_text(aset.to_json(), encoding="utf-8")
    return client, path


def _run(*args, stdin=PASSWORD + "\n"):
    with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        try:
            call_command("studies", *args)
            return 0
        except SystemExit as exc:
            return exc.code


def test_the_password_never_reaches_argv_env_run_dir_or_logs(wired, tmp_path, caplog, capsys, monkeypatch):
    client, path = wired
    caplog.set_level(logging.DEBUG)
    run_dir = tmp_path / "run"
    argv = ["--mode", "plan", "--associations", str(path), "--seek-login", "operator", "--seek-password-stdin",
            "--run-dir", str(run_dir), "--json"]
    assert _run(*argv) == 0
    encoded = base64.b64encode(f"operator:{PASSWORD}".encode("utf-8")).decode("ascii")
    assert PASSWORD not in " ".join(argv) and PASSWORD not in " ".join(sys.argv)
    assert all(PASSWORD not in value and encoded not in value for value in os.environ.values())
    files = [f for f in run_dir.rglob("*") if f.is_file()]
    assert files
    for f in files:
        data = f.read_bytes()
        assert PASSWORD.encode("utf-8") not in data and encoded.encode("ascii") not in data, f.name
    out, err = capsys.readouterr()
    for text in (caplog.text, out, err):
        assert PASSWORD not in text and encoded not in text
    assert client.auth and set(client.auth) == {"Basic " + encoded}


def test_no_option_carries_the_password(wired):
    parser = cmd.Command().create_parser("manage.py", "studies")
    password_options = [a for a in parser._actions if "password" in a.dest]
    assert [(a.dest, a.nargs) for a in password_options] == [("seek_password_stdin", 0)]
    with pytest.raises((CommandError, SystemExit)):
        call_command("studies", "--mode", "plan", "--seek-password", PASSWORD)


def test_plan_writes_the_run_directory_and_says_what_is_next(wired, tmp_path, capsys):
    _client, path = wired
    assert _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator",
                "--seek-password-stdin", "--run-dir", str(tmp_path / "run")) == 0
    assert (tmp_path / "run" / "plan.txt").exists()
    assert "--mode apply --run-dir" in capsys.readouterr().out


def test_plan_needs_one_source_a_login_and_a_new_run_directory(wired, tmp_path):
    _client, path = wired
    assert _run("--mode", "plan", "--seek-login", "operator", "--seek-password-stdin") == 2
    assert _run("--mode", "plan", "--associations", str(path), "--seek-password-stdin") == 2
    run = tmp_path / "run"
    assert _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator",
                "--seek-password-stdin", "--run-dir", str(run)) == 0
    assert _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator",
                "--seek-password-stdin", "--run-dir", str(run)) == 2
    with pytest.raises((CommandError, SystemExit)):
        call_command("studies", "--mode", "plan", "--associations", str(path), "--sheet", "x.csv")


def test_the_default_run_directory_is_under_log_dir(wired, settings):
    _client, path = wired
    assert _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator",
                "--seek-password-stdin") == 0
    [made] = list((cmd.Path(settings.LOG_DIR) / "studies").iterdir())
    assert made.name.endswith("-replay")


def test_a_planner_defect_exits_2_and_writes_nothing(wired, tmp_path, monkeypatch):
    _client, path = wired

    def defect(*args, **kwargs):
        raise planner.PlannerDefect([])

    monkeypatch.setattr(planner, "plan_study_moves", defect)
    assert _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator",
                "--seek-password-stdin", "--run-dir", str(tmp_path / "run")) == 2
    assert not (tmp_path / "run").exists()


@pytest.mark.django_db
def test_apply_proves_the_login_first_and_maps_the_status_to_the_exit(wired, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cmd.SeekSession, "prove", lambda self: seen.append("prove") or self)
    for status, code in ((a.DONE, 0), (a.STOPPED, 1), (a.REFUSED, 2)):
        monkeypatch.setattr(a, "apply_study_moves",
                            lambda *args, status=status, **kw: seen.append("apply") or a.RunResult(status, "m"))
        assert _run("--mode", "apply", "--run-dir", str(tmp_path), "--seek-login", "operator",
                    "--seek-password-stdin") == code
    assert seen[:2] == ["prove", "apply"]
    assert _run("--mode", "apply", "--seek-login", "operator", "--seek-password-stdin") == 2


def test_graph_needs_the_approval_and_the_live_flag_on_the_live_graph(wired, tmp_path, monkeypatch, settings):
    monkeypatch.setattr(a, "graph_step", lambda *args, **kw: a.RunResult(a.DONE, "ok"))
    assert _run("--mode", "graph", "--run-dir", str(tmp_path)) == 2
    settings.NEO4J_DATABASE = {"NAME": "neo4j", "URI": "bolt://neo4j:7687", "AUTH": ("neo4j", "x")}
    assert _run("--mode", "graph", "--run-dir", str(tmp_path), "--approve-label-changes") == 2
    assert _run("--mode", "graph", "--run-dir", str(tmp_path), "--approve-label-changes",
                "--i-mean-the-live-graph") == 0


def test_rollback_is_a_dry_run_without_confirm(wired, tmp_path, monkeypatch, settings):
    from nextseek_api.studies import rollback

    seen = []
    monkeypatch.setattr(cmd.SeekSession, "prove", lambda self: self)
    monkeypatch.setattr(rollback, "rollback_study_moves",
                        lambda *args, confirm, investigation=None: seen.append(confirm) or a.RunResult(a.DONE, "m"))
    assert _run("--mode", "rollback", "--run-dir", str(tmp_path), "--seek-login", "operator",
                "--seek-password-stdin") == 0
    assert _run("--mode", "rollback", "--run-dir", str(tmp_path), "--seek-login", "operator",
                "--seek-password-stdin", "--confirm") == 0
    assert seen == [False, True]


def test_json_puts_only_the_result_on_stdout(wired, tmp_path, capsys):
    _client, path = wired
    _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator", "--seek-password-stdin",
         "--run-dir", str(tmp_path / "run"), "--json")
    result = json.loads(capsys.readouterr().out)
    assert (result["mode"], result["exit_code"]) == ("plan", 0) and result["run_dir"].endswith("run")


def test_export_and_report(wired, tmp_path, monkeypatch, capsys):
    from nextseek_api.studies.sources import dev_export

    monkeypatch.setattr(dev_export, "export_dev_graph",
                        lambda driver, db, out, study_ids=None: {"studies": 1, "out": str(out)})
    assert _run("--mode", "export", "--out", str(tmp_path / "dev.json"), "--json") == 0
    assert json.loads(capsys.readouterr().out)["counts"]["studies"] == 1
    assert _run("--mode", "export") == 2
    _client, path = wired
    _run("--mode", "plan", "--associations", str(path), "--seek-login", "operator", "--seek-password-stdin",
         "--run-dir", str(tmp_path / "run"))
    capsys.readouterr()
    assert _run("--mode", "report", "--run-dir", str(tmp_path / "run"), "--json") == 0
    assert json.loads(capsys.readouterr().out)["counts"]["units"]["planned"] == 1
