"""The session search's index: what each turn put in front of the user, pulled out once when the turn ends.

Two kinds of rows land in ``ChatSearchEntry`` (``nextseek_api/assistant/models_db.py``), read by
``nextseek_api/assistant/session_search.py``:

* ``steps``: a Container-CC turn's agent text and tool calls, from its stored transcript, minus any line that
  looks like a credential (the transcript was scrubbed of the real values before it was stored; this is a second,
  cruder net).
* ``record``: the sample and protocol IDs the turn showed: named in the question (``key_entities``), in the answer,
  or in its results (the NS bundle's result keys, or the CC tool output), the results capped at ``RESULT_CAP`` with
  the total kept on the turn's ``turn`` row; plus the projects the question named.

``index_chat`` runs at the end of every turn (``NessieAI/cc/turn.py``, ``NessieAI/ns/turn.py``) and from the
``chat_search_index`` backfill. It indexes only turns not yet indexed and drops the rows of turns that left the
chat's ``chat_log`` window, so it is cheap to call often and safe to call twice. The extractors above it are pure
and need no Django.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

RESULT_CAP = 500
#: The NS bundle keys that hold what the answer showed. Never ``api_result_full`` (the whole payload, spilled).
RESULT_KEYS = ("graph_result", "api_result_slim", "report_writer_output", "reporter_result")

# Sample IDs (nextseek_api/batch_upload/helpers.py UID_RE, found inside text) and protocol IDs, as the chat panel
# links them (NessieAI/chat_frontend/src/lib/remark-uid-links.ts).
_UID = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(P\.[A-Z]{2,5}-\d{6}-V\d+_[\w.+\-]+?(?:\.\w{2,5})?|(?:[A-Z]\.)?[A-Z]{2,}-\d{6}[A-Z]{2,5}-\d+(?:-PUB\d*)?)"
    r"(?![A-Za-z0-9])"
)
# A line that names a credential, or carries one in a URL or a curl -u, is dropped whole.
_SECRET = re.compile(
    r"authorization|bearer\s|nextseekturn|passw(?:or)?d|\bpass\b|token[\"']?\s*[=:]|api[_-]?key|secret"
    r"|://[^/\s:@]+:[^/\s@]+@|\s-u\s*\S+:\S+",
    re.IGNORECASE,
)
#: For a tool other than Bash, Read and Write only these input fields are kept (never file bodies or edits).
_TOOL_FIELDS = ("file_path", "pattern", "path", "skill", "url")


def find_uids(text: Any) -> list[str]:
    """The IDs in ``text``, in order of first appearance, each once."""
    if not isinstance(text, str):
        return []
    return list(dict.fromkeys(_UID.findall(text)))


def uid_type(uid: str) -> str:
    """The type code: what comes before the first dash (``TIS``, ``D.SEQ``, ``P.FLY``)."""
    return uid.split("-", 1)[0]


def _clean(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if line.strip() and not _SECRET.search(line))


def _tool_call(block: dict) -> str:
    """``Name: what it ran or touched``: Bash's command; for every other tool its paths and patterns only."""
    name, args = block.get("name") or "tool", block.get("input")
    if not isinstance(args, dict):
        return name
    if name == "Bash":
        value = args.get("command")
        return f"{name}: {value}" if isinstance(value, str) else name
    kept = [args[k] for k in _TOOL_FIELDS if isinstance(args.get(k), str)]
    return f"{name}: {' '.join(kept)}" if kept else name


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))
    return ""


def transcript_steps(jsonl: bytes) -> tuple[str, str, list[str]]:
    """(the agent's words, its tool calls, the IDs in its tool output) from one stored transcript."""
    texts, calls, uids = [], [], []
    for line in (jsonl or b"").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get("message") or {}).get("content") if isinstance(rec, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and rec.get("type") == "assistant" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif kind == "tool_use":
                calls.append(_tool_call(block))
            elif kind == "tool_result":
                uids.extend(find_uids(_result_text(block.get("content"))))
    return _clean("\n".join(texts)), _clean("\n".join(calls)), list(dict.fromkeys(uids))


def _texts(value: Any):
    """Every string inside ``value`` (dict values, list items), walked in order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)


def bundle_result_uids(bundle: Any) -> list[str]:
    """The IDs an NS answer showed: those in its result keys, in order, each once. Strings are searched one by
    one: an ID that starts a line inside one is found (``json.dumps`` would hide it behind ``\\n``)."""
    if not isinstance(bundle, dict):
        return []
    out = [u for key in RESULT_KEYS for text in _texts(bundle.get(key)) for u in find_uids(text)]
    return list(dict.fromkeys(out))


def _strings(values: Any) -> list[str]:
    return [v for v in values if isinstance(v, str) and v] if isinstance(values, list) else []


def _shown(entry: dict) -> bool:
    """A turn the chat shows (session_export.turn_rows hides one with no reply, preview or bundle: PD-6)."""
    reply = entry.get("assistant_reply") or entry.get("assistant_reply_preview")
    return isinstance(entry.get("user_query"), str) and bool(entry["user_query"]) and (
        isinstance(reply, str) and bool(reply) or entry.get("bundle_id") is not None)


def turn_rows(entry: dict, bundle: Any, steps: Iterable[tuple[str, str, list[str]]]) -> list[dict]:
    """The index rows of one chat_log turn, as field dicts (``session``, ``user`` and ``turn_id`` added by the caller)."""
    steps = list(steps)
    entities = entry.get("key_entities") if isinstance(entry.get("key_entities"), dict) else {}
    reply = entry.get("assistant_reply") or entry.get("assistant_reply_preview")
    named = {"question": [u for s in _strings(entities.get("uids")) for u in find_uids(s)], "answer": find_uids(reply)}
    results = list(dict.fromkeys(bundle_result_uids(bundle) + [u for s in steps for u in s[2]]))
    rows = []
    for where, uids in (*named.items(), ("result", results[:RESULT_CAP])):
        for uid in uids:  # each list is already distinct: an ID in the answer and the results counts as both
            if len(uid) <= 64:
                rows.append({"source": "record", "kind": "uid", "key": uid, "record_type": uid_type(uid)[:16],
                             "text": where})
    for project in dict.fromkeys(_strings(entities.get("projects"))):
        rows.append({"source": "record", "kind": "project", "key": project[:64]})
    agent_text = "\n".join(s[0] for s in steps if s[0])
    tool_calls = "\n".join(s[1] for s in steps if s[1])
    if agent_text:
        rows.append({"source": "steps", "kind": "agent_text", "text": agent_text})
    if tool_calls:
        rows.append({"source": "steps", "kind": "tool_call", "text": tool_calls})
    rows.append({"source": "record", "kind": "turn", "text": str(len(results))})
    return rows


def _stored_bundles(chat_session, ids: set) -> dict:
    """{bundle id: the bundle with only its result keys read back} for ``ids``, from the stored column.

    Read as text (the column's spill links left as they are), then only the result keys of the wanted bundles go
    through the size guard's hydrate: the full payload of a big GEO answer is never loaded to index it.
    """
    from django.db.models import TextField
    from django.db.models.functions import Cast

    from nextseek_api.assistant.models_db import ChatSession
    from nextseek_api.assistant.spill import hydrate

    raw = (ChatSession.objects.order_by().filter(pk=chat_session.pk)
           .annotate(raw=Cast("results_history", TextField())).values_list("raw", flat=True).first())
    stored = json.loads(raw) if raw else []
    if isinstance(stored, dict):  # the whole column is one spill link: it comes back whole
        stored = hydrate(stored)
    out = {}
    for b in stored if isinstance(stored, list) else []:
        if isinstance(b, dict) and b.get("id") in ids:
            out[b["id"]] = {k: hydrate(b[k]) for k in RESULT_KEYS if b.get(k) is not None}
    return out


def index_chat(chat_session, *, chat_log: list | None = None, bundles: list | None = None) -> int:
    """Index the chat's turns that have no rows yet, and drop the rows of turns no longer in its chat_log.

    ``chat_log`` and ``bundles`` may be handed in from memory (the NS turn's session adapter); otherwise they are
    read from the row, the bundles' result keys only and only when a turn to index has one. The rows are built
    first and written under the chat row's lock, which skips any turn another run indexed meanwhile (two runs at
    once wrote every row twice on MySQL, review 10-26). Returns the number of rows written.
    """
    from django.db import transaction

    from NessieAI.cc.cc_transcript_store import decompress
    from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSearchEntry, ChatSession

    if chat_log is None:
        chat_session.refresh_from_db(fields=["extra_state"])
        chat_log = (chat_session.extra_state or {}).get("chat_log")
    entries = {e["turn_id"]: e for e in chat_log if isinstance(e, dict) and isinstance(e.get("turn_id"), int)
               and _shown(e)} if isinstance(chat_log, list) else {}

    def indexed_turns():
        return set(ChatSearchEntry.objects.filter(session=chat_session, source="record", kind="turn")
                   .values_list("turn_id", flat=True))

    todo = [e for t, e in entries.items() if t not in indexed_turns()]
    new: list = []
    if todo:
        wanted = {e.get("bundle_id") for e in todo if e.get("bundle_id") is not None}
        if bundles is not None:
            by_id = {b.get("id"): b for b in bundles if isinstance(b, dict)}
        else:
            by_id = _stored_bundles(chat_session, wanted) if wanted else {}

        steps: dict[str, list] = {}
        run_ids = [e["cc_run_id"] for e in todo if isinstance(e.get("cc_run_id"), str)]
        if run_ids:
            for run_id, blob in (CCSessionTranscript.objects.order_by()
                                 .filter(chat_session=chat_session, turn_id__in=run_ids)
                                 .values_list("turn_id", "blob")):
                try:
                    steps.setdefault(run_id, []).append(transcript_steps(decompress(bytes(blob))))
                except Exception:  # noqa: BLE001 - one unreadable transcript must not cost the chat its index
                    logger.warning("chat %s: transcript %s could not be read for the search index",
                                   chat_session.pk, run_id, exc_info=True)
        new = [
            ChatSearchEntry(session=chat_session, user_id=chat_session.user_id, turn_id=e["turn_id"], **row)
            for e in todo
            for row in turn_rows(e, by_id.get(e.get("bundle_id")), steps.get(e.get("cc_run_id"), []))
        ]

    with transaction.atomic():
        ChatSession.objects.select_for_update().filter(pk=chat_session.pk).values_list("pk").first()
        rows = ChatSearchEntry.objects.filter(session=chat_session)
        rows.exclude(turn_id__in=list(entries)).delete()
        done = indexed_turns()
        new = [r for r in new if r.turn_id not in done]
        rows.filter(turn_id__in={r.turn_id for r in new}).delete()  # a half-written turn (no turn row) starts over
        ChatSearchEntry.objects.bulk_create(new, batch_size=1000)
    return len(new)


def index_chat_quietly(chat_session, **kwargs) -> None:
    """``index_chat`` at a turn's end: an error is logged and the turn goes on (the backfill repairs the index)."""
    try:
        index_chat(chat_session, **kwargs)
    except Exception:  # noqa: BLE001 - search is never worth a failed turn
        logger.warning("chat %s: the search index was not updated", getattr(chat_session, "pk", "?"), exc_info=True)
