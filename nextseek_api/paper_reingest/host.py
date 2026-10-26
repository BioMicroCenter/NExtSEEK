"""The seams paper-reingest-start runs with (NessieAI/ns/paper/start.py).

The attached PDF is read from the caller's own CC input folder, which only Django's
upload task writes, by a single validated name and never through a link.
"""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 100 * 1024 * 1024
#: What the launch check leaves of the op's limit for the reply's trip back.
ANSWER_MARGIN_S = 5.0
QUEUE_UNAVAILABLE = "The run could not be queued; try again shortly."


def enabled() -> bool:
    return os.environ.get("NEXTSEEK_PAPER_REINGEST") == "1"


def read_input(input_dir: str, name: str) -> bytes:
    from NessieAI.cc.cc_upload_validate import validate_upload_filename
    from NessieAI.ns.paper.launch import AttachmentTooLarge

    safe = validate_upload_filename(name)
    fd = os.open(os.path.join(input_dir, safe), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise AttachmentTooLarge(f"{safe} is larger than {MAX_PDF_BYTES // 2**20} MB")
    return data


def could_name_a_file(paper: str) -> bool:
    """Could ``paper`` be the name of an attached file? A PMID, PMCID, DOI or article link cannot, so its call
    never looks up the caller's input folder (a SEEK round trip). An attachment named only by a number (``12345``)
    is read as a PMID, so it is never matched as a file; ``12345.pdf`` is."""
    from NessieAI.ns.paper.identify import IdentifyError, identify

    text = (paper or "").strip()
    if not text or "/" in text or "\\" in text:
        return False
    if text.lower().endswith(".pdf"):
        return True
    try:
        identify(text, set())
    except IdentifyError:
        return True
    return False


def time_left() -> float | None:
    """Seconds the launch check has left: the op's limit as the active call_scope has it now, less
    ANSWER_MARGIN_S. None when the scope says nothing more should start."""
    from chat_nextseek import call_scope

    from NessieAI.ns.op_limits import OP_LIMITS_S

    left = call_scope.time_left_for(OP_LIMITS_S["paper-reingest-start"])
    return None if left is None else left - ANSWER_MARGIN_S


def launch_fetcher():
    """The launch check's Fetcher: no retries, and one deadline (time_left) for every request it makes."""
    from NessieAI.ns.paper.fetch import Fetcher

    return Fetcher(retries=0, time_left=time_left)


def _input_dir(request) -> str | None:
    from NessieAI.cc.cc_config import CCPaths
    from NessieAI.cc.cc_provision import ProjectResolutionError, build_user_dirs, resolve_user_project
    from nextseek_api.services.assistant import _request_login

    username, password = _request_login(request)
    try:
        project = resolve_user_project(username, password)
    except ProjectResolutionError:
        return None
    return build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username).input_mnt


def queue_or_fail(run) -> None:
    """Queue stage 1, or fail the run at ``queue`` and refuse the start: a run the queue never heard of
    must not sit in ``queued`` for ever."""
    from NessieAI.ns.paper.start import StartRefused
    from nextseek_api.paper_reingest import service

    try:
        service.enqueue_acquire(run)
    except Exception as exc:
        logger.exception("paper reingest %s: could not queue stage 1", run.run_id)
        service.set_state(run, "failed", failed_stage="queue", reason=f"could not queue: {type(exc).__name__}")
        raise StartRefused(QUEUE_UNAVAILABLE, "queue_unavailable") from exc


def _queue_after_commit(run) -> None:
    # The op has answered by the time a deferred commit runs, so the refusal has no one to reach; the run is
    # failed already, and an exception here would only break the code that committed.
    from NessieAI.ns.paper.start import StartRefused

    try:
        queue_or_fail(run)
    except StartRefused:
        pass


def start_session(request) -> SimpleNamespace:
    from django.db import transaction

    from NessieAI.cc.cc_upload_list import list_input_files
    from NessieAI.ns.paper.launch import LaunchSeams
    from nextseek_api.assistant import chat_turns
    from nextseek_api.paper_reingest import service
    from nextseek_api.paper_reingest.lookups import doi_project_ids_strict

    user = request.user

    def launch_seams(paper: str) -> LaunchSeams:
        input_dir = _input_dir(request) if could_name_a_file(paper) else None
        files = set(list_input_files(input_dir)) if input_dir else set()
        return LaunchSeams(fetcher=launch_fetcher(), input_files=files,
                           read_input_file=lambda name: read_input(input_dir, name),
                           doi_project_ids=doi_project_ids_strict)

    def create_run(**kwargs):
        turn = chat_turns.running_cc_turn(user) or {}
        return service.create_run(user=user, chat_session_id=turn.get("session_id", ""), **kwargs)

    def enqueue_acquire(run) -> None:
        # Stage 1 must never see a run row that is not committed yet. The op view runs in autocommit
        # (no ATOMIC_REQUESTS): the row is committed, so queue now and let a queue_unavailable refusal reach
        # the reply. Inside a transaction, wait for the commit; a failure then only fails the run.
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(lambda: _queue_after_commit(run))
        else:
            queue_or_fail(run)

    return SimpleNamespace(
        user=user, enabled=enabled, launch_seams=launch_seams,
        unfinished_run=lambda doi, raw: service.unfinished_run_for(user, doi, raw),
        create_run=create_run, enqueue_acquire=enqueue_acquire, time_left=time_left)
