"""The dedicated `attribute_mutations` Celery task (DD-29). Bound to the
namesake queue only -- the shared `batch_upload` worker never consumes it
(M-WORKER-01) -- with late acknowledgment and worker-loss rejection so an
in-flight message a killed worker never acked is redelivered rather than
silently dropped; `run_stored_job`'s job-level lease/CAS makes that
redelivery idempotent (M-DELIVERY-01).
"""
from __future__ import annotations

import socket

from nextseek_api.batch_upload.celery_app import app


@app.task(bind=True, name="attribute_mutations.run", queue="attribute_mutations", acks_late=True, reject_on_worker_lost=True)
def run_attribute_mutation(self, job_id: str) -> dict:
    from nextseek_api.attributes.jobs import mutation_job_store, run_stored_job

    owner = f"worker:{socket.gethostname()}:{self.request.id}"
    return run_stored_job(job_id, mutation_job_store(), owner)
