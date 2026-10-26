"""The body of paper-reingest-start (spec §3.1), behind seams the host hands in.

Curators only (is_superuser) and only where NEXTSEEK_PAPER_REINGEST=1 (spec §12). The
launch check runs here, in the turn; nothing is queued unless it passes.
"""
from __future__ import annotations

from NessieAI.ns.paper.identify import IdentifyError
from NessieAI.ns.paper.launch import launch_check


class StartRefused(ValueError):
    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


def start(paper: str, project: int | None, session) -> dict:
    if not getattr(getattr(session, "user", None), "is_superuser", False):
        raise StartRefused("paper reingest is open to curators (superusers) only", "superusers_only")
    if not session.enabled():
        raise StartRefused("paper reingest is switched off on this instance", "feature_off")
    try:
        outcome = launch_check(paper, session.launch_seams())
    except IdentifyError as exc:
        raise StartRefused(str(exc), "unrecognised_paper") from exc
    if outcome.status != "ok":
        return {"run_id": None, "status": outcome.status, "missing": outcome.missing, "reply": outcome.reply}
    ref = outcome.paper.ref
    existing = session.unfinished_run(ref.doi, paper)
    if existing is not None:
        return {"run_id": existing.run_id, "status": "already_running", "state": existing.state,
                "reply": f"A run for this paper is already {existing.state}: {existing.run_id}."}
    run = session.create_run(paper_input=paper, paper=outcome.paper, project_hint=project)
    session.enqueue_acquire(run)
    note = ""
    if outcome.paper.mode == "update":
        listed = ", ".join(str(p) for p in outcome.paper.existing_project_ids)
        note = f" It is already in NExtSEEK (projects {listed}), so this run will propose updates, never deletions."
    return {"run_id": run.run_id, "status": "ok", "mode": outcome.paper.mode,
            "paper": {"pmid": ref.pmid, "pmcid": ref.pmcid, "doi": ref.doi, "title": ref.title},
            "reply": (f"Started paper reingest {run.run_id} for “{ref.title}”.{note} "
                      "I'm reading the paper, its supplement and its deposits in the background.")}
