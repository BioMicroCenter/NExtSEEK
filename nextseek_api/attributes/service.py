"""View-facing composition facade for native attribute reads and mutations."""
from __future__ import annotations

import re
import uuid

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .auth import authenticate_seek_person
from .executor import execute_batch, execution_services_factory
from .jobs import JobHeartbeat, MutationJobService, _overall_status_and_http, mutation_job_store
from .pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from .planner import MutationPlanner
from .repository import AttributeRepository, SeekAttributeGateway
from .resolver import ResolutionError
from .scalars import parse_query_positive_int
from .schemas import (
    AttributeErrorResponse,
    AttributeListResponse,
    AttributeRecord,
    AutomaticChange,
    MutationAcceptedResponse,
    MutationCompletedResponse,
    MutationCounts,
    MutationError,
    MutationJobStatusResponse,
    MutationPreviewResponse,
    SampleTypeMutationOutcome,
)


def _page(query) -> PageRequest:
    return PageRequest(
        page=parse_query_positive_int(query, "page", default=1, maximum=2**31 - 1),
        page_size=parse_query_positive_int(
            query, "page_size", default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE,
        ),
    )


def _page_response(page) -> dict:
    return AttributeListResponse(
        attributes=[AttributeRecord.model_validate(value) for value in page.attributes],
        pagination=page.pagination,
    ).model_dump(mode="json")


def _counts(plan) -> MutationCounts:
    values = {key: 0 for key in MutationCounts.model_fields}
    for item in plan.types:
        for key, value in item.counts.items():
            if key in values:
                values[key] += value
    return MutationCounts(**values)


HYPOTHETICAL_PREVIEW_KEYS = {
    "token", "title", "sample_type_id", "sample_type_title",
    "sample_attribute_type_id", "sample_attribute_type_title", "required", "pos",
    "is_title", "description", "unit_id", "unit_title", "unit_symbol",
    "sample_controlled_vocab_id", "sample_controlled_vocab_title",
    "linked_sample_type_id", "linked_sample_type_title",
}


def _hypothetical_create_change(record) -> AutomaticChange:
    if set(record) != HYPOTHETICAL_PREVIEW_KEYS:
        raise ValueError("T05 hypothetical preview shape drift")
    if not re.fullmatch(r"created:\d+:\d+", str(record["token"])):
        raise ValueError("invalid T05 created identity token")
    if record["sample_type_id"] is None or not record["title"]:
        raise ValueError("hypothetical preview lacks resolved public identity")
    return AutomaticChange(
        kind="create_preview", attribute_id=None, attribute_title=record["title"],
        field="definition", previous_value=None, new_value=dict(record),
    )


def _preview(plan) -> MutationPreviewResponse:
    outcomes = []
    for item in plan.types:
        if item.sample_type_id is None:
            continue
        persisted = [AttributeRecord.model_validate(record) for record in item.preview_records]
        hypothetical = [_hypothetical_create_change(record) for record in item.hypothetical_preview_records]
        if item.status in {"failed", "plan_delta_required"}:
            public_status = "failed"
        elif item.status == "unchanged":
            public_status = "unchanged"
        else:
            public_status = "succeeded"
        outcomes.append(SampleTypeMutationOutcome(
            sample_type_id=item.sample_type_id,
            sample_type_title=item.sample_type_title,
            status=public_status,
            counts=MutationCounts.model_validate(item.counts),
            attributes=persisted,
            automatic_changes=[AutomaticChange.model_validate(change) for change in item.automatic_changes] + hypothetical,
            errors=[MutationError.model_validate(error) for error in item.errors],
        ))
    statuses = {item.status for item in outcomes}
    executable = bool(statuses & {"succeeded", "unchanged"})
    blocked = bool(statuses & {"failed", "cancelled", "skipped"})
    overall = "partial" if executable and blocked else "succeeded" if executable else "failed"
    return MutationPreviewResponse(
        mode="dry_run", predicted_mode=plan.predicted_mode, overall_status=overall,
        threshold=plan.active_threshold, counts=_counts(plan), outcomes=outcomes,
    )


def _completed(mode: str, outcomes) -> MutationCompletedResponse:
    normalized = [SampleTypeMutationOutcome.model_validate(value) for value in outcomes]
    overall_status, http_status = _overall_status_and_http(
        [value.model_dump(mode="json") for value in normalized], False,
    )
    counts = MutationCounts(**{
        field: sum(getattr(value.counts, field) for value in normalized)
        for field in MutationCounts.model_fields
    })
    return MutationCompletedResponse(
        mode=mode, overall_status=overall_status, http_status=http_status,
        counts=counts, outcomes=normalized,
    )


def _resolution_error(exc: ResolutionError) -> dict:
    return AttributeErrorResponse(errors=[MutationError(
        code=exc.code, message=str(exc), target_index=exc.target_index,
        attribute_index=exc.attribute_index, field=exc.field,
        submitted_identifier=exc.submitted_identifier,
    )]).model_dump(mode="json")


class AttributeServices:
    def __init__(self, repository, planner, jobs, executor=execute_batch):
        self.repository = repository
        self.planner = planner
        self.jobs = jobs
        self.executor = executor

    @classmethod
    def build(cls):
        repository = AttributeRepository(SeekAttributeGateway())
        threshold = settings.ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD
        return cls(repository, MutationPlanner(threshold=threshold), MutationJobService())

    def list(self, query):
        return _page_response(self.repository.catalog(_page(query)))

    def retrieve(self, attribute_id):
        try:
            return self.repository.retrieve(attribute_id).model_dump(mode="json")
        except ResolutionError as exc:
            if exc.code == "attribute_not_found":
                return None
            raise

    def search(self, payload, query):
        targets = [target.model_dump(mode="python", exclude_unset=True) for target in payload.targets]
        try:
            return _page_response(self.repository.search(targets, _page(query)))
        except ResolutionError as exc:
            return _resolution_error(exc)

    def mutate(self, operation, payload, dry_run, request):
        person = authenticate_seek_person(request)
        envelope = payload.model_dump(mode="python", exclude_unset=True)
        envelope.update({"kind": operation, "actor": person.to_json()})
        plan = self.planner.plan_mutation(envelope, self.repository)
        unresolved = [item for item in plan.types if item.sample_type_id is None]
        if unresolved:
            errors = [
                MutationError.model_validate(error)
                for item in unresolved for error in item.errors
            ] or [MutationError(
                code="unresolved_sample_type",
                message="Planner returned an unresolved sample type without structured detail.",
            )]
            return AttributeErrorResponse(errors=errors).model_dump(mode="json"), 409
        if dry_run:
            body = _preview(plan).model_dump(mode="json")
            code = 200 if body["overall_status"] == "succeeded" else 207 if body["overall_status"] == "partial" else 409
            return body, code

        # With no executable partition, the shared adapter can render a complete
        # response without creating an impossible empty durable job.
        if not plan.executable_types:
            completed = _completed("synchronous", self.executor(plan.types, lambda _plan: None))
            return completed.model_dump(mode="json"), completed.http_status

        job = self.jobs.create(plan, person.to_json(), plan.predicted_mode)
        if plan.predicted_mode == "asynchronous":
            body = MutationAcceptedResponse(
                mode="asynchronous", job_id=job.job_id,
                status_url=reverse("nextseek_api:attribute-job", kwargs={"job_id": job.job_id}),
                counts=_counts(plan),
            )
            return body.model_dump(mode="json"), 202

        store = mutation_job_store()
        owner = f"sync:{uuid.uuid4()}"
        lease = store.start_job(str(job.job_id), owner)
        if lease is None:
            raise RuntimeError("synchronous job claim was not acquired")
        heartbeat = JobHeartbeat(store, lease).start()
        try:
            heartbeat.wait_for_first_renewal()
            outcomes = self.executor(
                plan.types,
                execution_services_factory(job),
                max_workers=settings.ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM,
            )
            store.record_progress(lease, len(outcomes), len(plan.types), outcomes)
            completed = _completed("synchronous", outcomes)
            store.finish(lease, completed.overall_status, completed.model_dump(mode="json"))
        finally:
            heartbeat.stop()
        return completed.model_dump(mode="json"), completed.http_status

    def get_job_object(self, job_id):
        return mutation_job_store().get_job(job_id)

    def get_job(self, job_id, request):
        job = self.get_job_object(job_id)
        partitions = list(job.partitions.all())
        result = job.terminal_result
        if result is not None:
            normalized_result = MutationCompletedResponse.model_validate(result)
            total_types = len(normalized_result.outcomes)
            total_samples = normalized_result.counts.affected_samples
            completed_types = total_types
            processed_samples = total_samples
            state = job.state
        else:
            normalized_result = None
            from .jobs import _replan
            live_plan = _replan(job)
            total_types = len(live_plan.types)
            completed = [item.outcome for item in partitions if item.outcome]
            completed_types = len(completed)
            processed_samples = sum(item.get("counts", {}).get("affected_samples", 0) for item in completed)
            # Re-plan supplies the accepted denominator without duplicating it
            # into mutable outbox state.
            total_samples = live_plan.affected_sample_rows
            state = "queued" if job.state in {"accepted", "queued"} else job.state
        return MutationJobStatusResponse(
            job_id=job.job_id, state=state, completed_sample_types=completed_types,
            total_sample_types=total_types, processed_samples=processed_samples,
            total_samples=total_samples, result=normalized_result,
        ).model_dump(mode="json")

    def cancel_job(self, job_id, request):
        person = authenticate_seek_person(request)
        job = self.get_job_object(job_id)
        if job.state not in {"accepted", "queued", "running"} or job.cancellation_requested_at is not None:
            return AttributeErrorResponse(errors=[MutationError(
                code="not_cancellable", message="Job is terminal or cancellation was already requested",
            )]).model_dump(mode="json"), 409
        now = timezone.now()
        with transaction.atomic():
            updated = type(job).objects.filter(
                pk=job.pk, state_version=job.state_version,
                state__in=("accepted", "queued", "running"), cancellation_requested_at__isnull=True,
            ).update(
                cancellation={"requested_at": now.isoformat(), "actor_seek_person_id": person.person_id},
                cancellation_requested_at=now,
                cancellation_actor_seek_person_id=person.person_id,
                state_version=job.state_version + 1,
            )
        if updated != 1:
            return AttributeErrorResponse(errors=[MutationError(
                code="not_cancellable", message="Job is terminal or cancellation was already requested",
            )]).model_dump(mode="json"), 409
        return self.get_job(job_id, request), 202
