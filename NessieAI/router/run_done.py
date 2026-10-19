"""'My run is done' goes to Container-CC's reingest: the deterministic backstop.

The pipeline wizard's submit reply ends its "Watching this run" block by telling the user
to say "my run is done" once the run finishes (``format_luria_followup`` in
``chat_nextseek/pipeline/agent_tools.py``). That turn names no folder, and needs none:
CC's ``nextseek-run-harvest`` finds the caller's launched runs not yet reingested. On
NExtSEEK the same words reach the reporter, which can open a fresh pipeline wizard, so
``policy._decide_route`` sends a matching turn to CC whatever the router said, before the
pipeline gate can keep it.

A turn is a run-done announcement when it says that a run, pipeline or job is done,
finished or complete, and is neither a question ("is my run done?", "has it finished?")
nor a request to launch something ("when the run is done, launch rnaseq"). A question
about a run is left to the router: it may be a status check rather than a hand-off.

Pure: no Django, no BAML, no I/O, so it is importable anywhere the router is.
"""
from __future__ import annotations

import re

#: "my run is done", "the run finished", "my run's done", "my nf-core run has finished",
#: "my pipeline run completed", "my run in /net/.../runs/x is done".
_RUN_DONE = re.compile(
    r"\b(?:run|pipeline|job)(?:\s+(?:in|at|on)\s+\S+)?"
    r"(?:\s+(?:is|has|was)|['’]s)?\s+(?:now\s+|just\s+|finally\s+|all\s+)?(?:been\s+)?"
    r"(?:done|finished|complete|completed)\b",
    re.IGNORECASE,
)

#: The same with a short clause between the run and its verb: "the run you launched for
#: me has finished", "the job I submitted yesterday is done". The verb must be there.
_RUN_CLAUSE_DONE = re.compile(
    r"\b(?:run|pipeline|job)(?:\s+[^\s.,;:!?]+){1,5}?\s+(?:is|has|was)\s+"
    r"(?:now\s+|just\s+|finally\s+|all\s+)?(?:been\s+)?(?:done|finished|complete|completed)\b",
    re.IGNORECASE,
)

#: A turn that opens with one of these asks about the run rather than announcing it.
_QUESTION_OPENERS = frozenset({
    "is", "has", "have", "did", "does", "was", "were", "are", "will", "when", "whether",
    "if", "how", "what", "why", "can", "could",
})

#: A turn that asks for a launch is the wizard's, even when it mentions a finished run.
_LAUNCH = re.compile(
    r"\b(?:launch|start|submit|kick\s+off|set\s+up|configure|re-?run)\b", re.IGNORECASE)


def run_done_reason(query: str | None) -> str | None:
    """The matched phrase when ``query`` announces a finished run, else None."""
    text = (query or "").strip()
    if not text:
        return None
    words = text.split(None, 1)
    if words[0].lower().strip(",.!") in _QUESTION_OPENERS or _LAUNCH.search(text):
        return None
    match = _RUN_DONE.search(text) or _RUN_CLAUSE_DONE.search(text)
    return f"run-done: {match.group(0)!r}" if match else None
