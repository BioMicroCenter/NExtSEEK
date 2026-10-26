"""The seams paper-reingest-start runs with (NessieAI/ns/paper/start.py).

The attached PDF is read from the caller's own CC input folder, which only Django's
upload task writes, by a single validated name and never through a link.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

MAX_PDF_BYTES = 100 * 1024 * 1024


def enabled() -> bool:
    return os.environ.get("NEXTSEEK_PAPER_REINGEST") == "1"


def read_input(input_dir: str, name: str) -> bytes:
    from NessieAI.cc.cc_upload_validate import validate_upload_filename

    safe = validate_upload_filename(name)
    fd = os.open(os.path.join(input_dir, safe), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(f"{safe} is larger than {MAX_PDF_BYTES // 2**20} MB")
    return data


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


def start_session(request) -> SimpleNamespace:
    from django.db import transaction

    from NessieAI.cc.cc_upload_list import list_input_files
    from NessieAI.ns.paper.fetch import Fetcher
    from NessieAI.ns.paper.launch import LaunchSeams
    from nextseek_api.assistant import chat_turns
    from nextseek_api.paper_reingest import service
    from nextseek_api.paper_reingest.lookups import doi_project_ids_strict

    user = request.user

    def launch_seams() -> LaunchSeams:
        input_dir = _input_dir(request)
        files = set(list_input_files(input_dir)) if input_dir else set()
        return LaunchSeams(fetcher=Fetcher(), input_files=files,
                           read_input_file=lambda name: read_input(input_dir, name),
                           doi_project_ids=doi_project_ids_strict)

    def create_run(**kwargs):
        turn = chat_turns.running_cc_turn(user) or {}
        return service.create_run(user=user, chat_session_id=turn.get("session_id", ""), **kwargs)

    def enqueue_acquire(run) -> None:
        # Stage 1 must never see a run row that is not committed yet. The op view runs in autocommit
        # (no ATOMIC_REQUESTS), where on_commit runs at once; inside a transaction it waits for the commit.
        transaction.on_commit(lambda: service.enqueue_acquire(run))

    return SimpleNamespace(
        user=user, enabled=enabled, launch_seams=launch_seams,
        unfinished_run=lambda doi, raw: service.unfinished_run_for(user, doi, raw),
        create_run=create_run, enqueue_acquire=enqueue_acquire)
