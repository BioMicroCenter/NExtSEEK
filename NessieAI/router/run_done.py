"""'My run is done' goes to Container-CC's reingest: the deterministic backstop.

The pipeline wizard's submit reply ends its "Watching this run" block by telling the user
to say "my run is done" once the run finishes (``format_luria_followup`` in
``chat_nextseek/pipeline/agent_tools.py``). That turn names no folder, and needs none:
CC's ``nextseek-run-harvest`` finds the caller's launched runs not yet reingested. On
NExtSEEK the same words reach the reporter, which can open a fresh pipeline wizard.

Whether a turn hands a finished run off is the router's intent call (the run-done
paragraph of ``router.baml``). This module is only the backstop for the hand-off line
itself, for when the router or the keyword heuristic keeps it off Container-CC:
``policy._decide_route`` moves a turn to CC's reingest only when the whole message is the
announcement, alone or with a request to register or load the run's outputs. A turn that
also asks for something else ("the sequencing run is complete; how many D.SEQ samples are
in project 12?") is the router's, because the finished run is only its context; so are a
question about the run ("is my run done?"), a launch request, and a wet-lab run (a
sequencing, PCR or flow run), which has no nf-core outputs to register.

The test is fail-closed: a word it does not know leaves the turn to the router.

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

#: Words, paths and numbers, lowercased by the caller.
_TOKEN = re.compile(r"[/~][^\s,;!?]*|[a-z0-9][a-z0-9'’\-]*")

#: Ends a clause: the run's subject is the part of its own clause before the match.
_CLAUSE_END = re.compile(r"[.;:!?\n]")

#: A run named by one of these is a wet-lab run, not a pipeline the wizard launched.
_LAB_RUN_WORDS = frozenset({
    "sequencing", "sequencer", "pcr", "qpcr", "rt-qpcr", "flow", "cytometry", "facs", "sort",
    "gel", "western", "elisa", "plate", "instrument", "machine", "miseq", "nextseq", "novaseq",
    "hiseq", "illumina", "nanopore", "pacbio",
})

#: The subject of the run may carry a name ("my Luria run", "ok my rnaseq pipeline"), but
#: never one of these: a question word or a request is another request.
_REQUEST_WORDS = frozenset({
    "which", "what", "who", "whom", "whose", "where", "when", "why", "how", "many", "much",
    "find", "show", "list", "count", "give", "get", "tell", "search", "look", "compare",
    "plot", "make", "download", "export", "send", "explain", "describe", "summarize",
    "summarise", "samples", "sample", "protocol", "protocols", "project", "study",
})

#: Everything else in the message must be one of these: a greeting or thanks, when or how
#: the run ended, or the hand-off itself ("can you register its outputs?").
_HANDOFF_WORDS = frozenset({
    # greetings, thanks, filler
    "hi", "hey", "hello", "ok", "okay", "cool", "great", "nice", "yay", "hooray", "good",
    "news", "so", "well", "alright", "please", "pls", "plz", "thanks", "thank", "you", "ty",
    "now", "just", "finally", "all", "and", "then", "also", "a", "an", "the", "my", "our",
    "this", "that", "it", "its", "it's", "them", "their", "me", "us", "i", "we", "i'd",
    "we'd", "i'm", "let's", "lets",
    # when and how the run ended
    "overnight", "successfully", "today", "tonight", "yesterday", "earlier", "last", "night",
    "week", "weekend", "morning", "afternoon", "evening", "ago", "hour", "hours", "minute",
    "minutes", "day", "days", "already", "cleanly", "fine", "without", "with", "no", "any",
    "error", "errors", "issue", "issues", "problem", "problems", "on", "at", "luria",
    "cluster", "hpc",
    # the hand-off
    "can", "could", "would", "will", "go", "ahead", "do", "is", "are", "be", "ready", "to",
    "for", "in", "into", "back", "of", "from", "like", "want", "need", "register", "load",
    "reingest", "re-ingest", "ingest", "import", "harvest", "upload", "add", "record", "save",
    "store", "bring", "pull", "put", "registered", "loaded", "ingested", "imported",
    "uploaded", "added", "output", "outputs", "result", "results", "file", "files", "data",
    "nextseek", "run", "pipeline", "job", "nf-core", "nfcore",
})

#: At most this many words name the run before its verb ("ok my rnaseq", "My Luria").
_SUBJECT_WORDS = 4


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _only_handoff(text: str) -> bool:
    """True when every word of ``text`` is a greeting, a timing or the hand-off."""
    return all(t in _HANDOFF_WORDS or t[0] in "/~" or t.isdigit() for t in _tokens(text))


def run_done_reason(query: str | None) -> str | None:
    """The matched phrase when the whole of ``query`` announces a finished run, else None.

    None also when the message asks for anything besides registering the run's outputs:
    that turn is the router's.
    """
    text = (query or "").strip()
    if not text:
        return None
    words = text.split(None, 1)
    if words[0].lower().strip(",.!") in _QUESTION_OPENERS or _LAUNCH.search(text):
        return None
    match = _RUN_DONE.search(text) or _RUN_CLAUSE_DONE.search(text)
    if not match:
        return None
    before, after = text[:match.start()], text[match.end():]
    clause_starts = [m.end() for m in _CLAUSE_END.finditer(before)]
    cut = clause_starts[-1] if clause_starts else 0
    earlier, subject = before[:cut], _tokens(before[cut:])
    if (len(subject) > _SUBJECT_WORDS
            or any(t in _LAB_RUN_WORDS or t in _REQUEST_WORDS for t in subject)
            or any(t in _LAB_RUN_WORDS for t in _tokens(match.group(0)))
            or not _only_handoff(earlier) or not _only_handoff(after)):
        return None
    return f"run-done: {match.group(0)!r}"
