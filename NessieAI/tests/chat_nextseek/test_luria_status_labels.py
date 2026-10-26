import pytest

import chat_nextseek.luria.status_labels as sl

LE = {"user": "u", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"}


def test_render_label_keeps_one_value_per_line():
    assert sl.render_label({"a": "x\ny", "b": 2}) == "a: x y\nb: 2\n"


def test_launched_label_names_what_the_cleanup_reads():
    text = sl.launched_label(run_dir="/net/x/runs/r_1", work_dir="/net/x/work/r",
                             pipeline="nf-core/rnaseq", revision="3.22.2", slurm_job_id="4821",
                             accessions=["SRR1", "SRR2"], now=0,
                             env={"DJANGO_ALLOWED_HOSTS": "nextseek.example.org localhost"})
    assert text == ("run_dir: /net/x/runs/r_1\nwork_dir: /net/x/work/r\n"
                    "pipeline: nf-core/rnaseq\nrevision: 3.22.2\nslurm_job_id: 4821\n"
                    "accessions: SRR1,SRR2\nlaunched_at: 1970-01-01T00:00:00Z\n"
                    "launched_from: nextseek.example.org\n")


def test_launched_from_is_unknown_without_hosts():
    assert sl.launched_from({}) == "unknown"


def test_labels_parse_with_the_cleanup_reader(tmp_path):
    from chat_nextseek.luria import cleanup
    saved = tmp_path / "saved.txt"
    saved.write_text(sl.saved_label(pipeline_run_id=5, now=86400))
    label = cleanup.read_label(str(saved))
    assert cleanup.label_time(label, "saved_at", str(saved)) == 86400.0
    assert label["pipeline_run_id"] == "5"
    launched = tmp_path / "launched.txt"
    launched.write_text(sl.launched_label(run_dir="/r", work_dir="/w", pipeline="p",
                                          revision="1", slurm_job_id="9", accessions=[], now=0))
    assert cleanup.read_label(str(launched))["work_dir"] == "/w"


def test_readme_explains_the_labels():
    for word in ("launched.txt", "finished.txt", "saved.txt", "weekly cleanup"):
        assert word in sl.STATUS_README


def _fake_ssh(monkeypatch, calls, fail=False):
    monkeypatch.setattr(sl, "prepare_key", lambda key: "/tmp/no-such-key-file")

    def fake(luria_env, cmd, *, key_path, timeout=None):
        calls.append((cmd, timeout))
        if fail:
            raise RuntimeError("ssh down")
        return ""

    monkeypatch.setattr(sl, "ssh_run", fake)


def test_write_saved_label_writes_inside_the_run_folder(monkeypatch):
    calls = []
    _fake_ssh(monkeypatch, calls)
    assert sl.write_saved_label(LE, "/net/x/runs/r_1", 5) is True
    ((cmd, timeout),) = calls
    assert cmd.startswith("test -d /net/x/runs/r_1 && mkdir -p /net/x/runs/r_1/nessie_status"
                          " && printf %s ")
    assert cmd.endswith("> /net/x/runs/r_1/nessie_status/saved.txt")
    assert "pipeline_run_id: 5" in cmd and timeout == 30


@pytest.mark.parametrize("run_dir", ["/net/x/work/r", "/net/x/runs/../etc", "/net/x/runs", "",
                                     "/elsewhere/runs/r"])
def test_write_saved_label_refuses_anything_outside_runs(monkeypatch, run_dir):
    calls = []
    _fake_ssh(monkeypatch, calls)
    assert sl.write_saved_label(LE, run_dir, 5) is False
    assert calls == []


def test_write_saved_label_needs_luria_configured(monkeypatch):
    calls = []
    _fake_ssh(monkeypatch, calls)
    assert sl.write_saved_label({**LE, "key": None}, "/net/x/runs/r_1", 5) is False
    assert calls == []


def test_write_saved_label_never_raises(monkeypatch):
    calls = []
    _fake_ssh(monkeypatch, calls, fail=True)
    assert sl.write_saved_label(LE, "/net/x/runs/r_1", 5) is False
