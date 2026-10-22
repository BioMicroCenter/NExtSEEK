"""The resume checkpoint had no reader left; keep its plumbing gone."""
import importlib.util
import inspect

from nextseek_api.batch_upload import insert, orchestrator


def test_checkpoint_module_is_gone():
    assert importlib.util.find_spec("nextseek_api.batch_upload.checkpoint") is None


def test_no_resume_parameters():
    for fn in (insert.process_batches, orchestrator.run_batch_upload, orchestrator.run_batch_upload_multi):
        params = inspect.signature(fn).parameters
        for gone in ("resume_uid", "checkpoint_dir", "checkpoint_name"):
            assert gone not in params, (fn.__name__, gone)
