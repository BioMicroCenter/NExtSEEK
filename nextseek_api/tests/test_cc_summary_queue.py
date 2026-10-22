"""The Container-CC summary sweep has its own queue and worker; uploads and the other beat jobs keep batch_upload."""
from pathlib import Path

ENTRYPOINT = (Path(__file__).resolve().parents[2] / "docker" / "scripts" / "entrypoint.sh").read_text().replace("\\\n", " ")


def _queue(name):
    from nextseek_api.batch_upload.celery_app import app

    return app.amqp.router.route({}, name)["queue"].name


def test_sweep_routes_to_its_own_queue():
    assert _queue("cc_assistant.sweep_cc_summaries") == "cc_summaries"


def test_uploads_and_other_jobs_stay_on_batch_upload():
    for name in ("cc_assistant.upload", "batch_upload.run", "batch_upload.reap_stale_query_tasks",
                 "batch_upload.purge_durable_state", "batch_upload.clear_expired_sessions"):
        assert _queue(name) == "batch_upload", name


def test_entrypoint_starts_a_worker_for_the_sweep_queue():
    lines = [ln for ln in ENTRYPOINT.splitlines()
             if "celery" in ln and " worker" in ln and not ln.lstrip().startswith("#")]
    sweep = [ln for ln in lines if "-Q cc_summaries" in ln]
    assert len(sweep) == 1, lines
    assert "--concurrency=1" in sweep[0] and "--hostname=cc_summaries@%h" in sweep[0]
    assert any("-Q batch_upload" in ln for ln in lines)
