"""The body of paper-reingest-start (spec §3.1), behind seams the host hands in.

Curators only (is_superuser) and only where NEXTSEEK_PAPER_REINGEST=1 (spec §12). The
launch check runs here, in the turn; nothing is queued unless it passes.
"""
from __future__ import annotations

import json

from NessieAI.ns.paper.europepmc import UnreadableAnswer
from NessieAI.ns.paper.fetch import MIN_LEFT_S, HostNotAllowed, OutOfTime, SourceNotFound, SourceUnavailable
from NessieAI.ns.paper.identify import IdentifyError
from NessieAI.ns.paper.jats import JatsUnreadable
from NessieAI.ns.paper.launch import (AttachmentTooLarge, AttachmentUnreadable, CatalogUnavailable,
                                      PdfDoiLookupFailed, launch_check)


OUT_OF_TIME = "Reading the paper took longer than this step allows; try again."
BAD_ANSWER = "Europe PMC answered with something I can't read for this paper; check it, or attach the PDF."
PDF_BAD_ANSWER = ("Europe PMC answered with something I can't read when I looked up the DOI printed in the "
                  "attached PDF, so I can't tell whether the paper is already in NExtSEEK; try again later.")


class RunExists(Exception):
    """Raised by the host's create_run when another start of the paper got its row in first; ``run`` is that run."""

    def __init__(self, run):
        super().__init__(run.run_id)
        self.run = run


def _already_running(run) -> dict:
    return {"run_id": run.run_id, "status": "already_running", "state": run.state,
            "reply": f"A run for this paper is already in progress ({run.state}): {run.run_id}."}


class StartRefused(ValueError):
    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


def start(paper: str, project: int | None, session) -> dict:
    if not getattr(getattr(session, "user", None), "is_superuser", False):
        raise StartRefused("paper reingest is open to curators (superusers) only", "superusers_only")
    if not session.enabled():
        raise StartRefused("paper reingest is switched off on this instance", "feature_off")
    seams = session.launch_seams(paper)
    # Every expected launch failure is a refusal the curator can act on. The messages carry no path, URL or
    # server detail; the cause stays chained for the server log.
    try:
        outcome = launch_check(paper, seams)
    except OutOfTime as exc:
        raise StartRefused(OUT_OF_TIME, "out_of_time") from exc
    except IdentifyError as exc:
        raise StartRefused(str(exc), "unrecognised_paper") from exc
    except SourceUnavailable as exc:
        raise StartRefused(f"{exc.host} is not answering; try again later", "source_unavailable") from exc
    except HostNotAllowed as exc:
        raise StartRefused("a source sent me to a site paper reingest does not read", "source_not_allowed") from exc
    except PdfDoiLookupFailed as exc:  # the PDF is read; only the lookup of its DOI got a bad answer
        raise StartRefused(PDF_BAD_ANSWER, "source_bad_answer") from exc
    except (SourceNotFound, json.JSONDecodeError, UnreadableAnswer, JatsUnreadable) as exc:
        # a 4xx, a body that is not JSON, JSON of the wrong shape, full text that is not a readable JATS article
        raise StartRefused(BAD_ANSWER, "source_bad_answer") from exc
    except AttachmentTooLarge as exc:
        raise StartRefused(str(exc), "pdf_too_large") from exc
    except AttachmentUnreadable as exc:
        raise StartRefused(str(exc), "pdf_unreadable") from exc
    except CatalogUnavailable as exc:
        raise StartRefused(f"{exc}; try again later", "catalog_unavailable") from exc
    if outcome.status != "ok":
        return {"run_id": None, "status": outcome.status, "missing": outcome.missing, "reply": outcome.reply}
    ref = outcome.paper.ref
    existing = session.unfinished_run(ref.doi, paper)
    if existing is not None:
        return _already_running(existing)
    # The op answers by its deadline; a check that ran past it must not leave a run the turn reported as failed.
    left = session.time_left()
    if left is None or left < MIN_LEFT_S:
        raise StartRefused(OUT_OF_TIME, "out_of_time")
    try:
        run = session.create_run(paper_input=paper, paper=outcome.paper, project_hint=project)
    except RunExists as exc:  # another curator's start of this paper won the database's unique key
        return _already_running(exc.run)
    session.enqueue_acquire(run)
    note = ""
    if outcome.paper.mode == "update":
        listed = ", ".join(str(p) for p in outcome.paper.existing_project_ids)
        note = f" It is already in NExtSEEK (projects {listed}), so this run will propose updates, never deletions."
    return {"run_id": run.run_id, "status": "ok", "mode": outcome.paper.mode,
            "paper": {"pmid": ref.pmid, "pmcid": ref.pmcid, "doi": ref.doi, "title": ref.title},
            "reply": (f"Started paper reingest {run.run_id} for “{ref.title}”.{note} "
                      "I'm reading the paper, its supplement and its deposits in the background.")}
