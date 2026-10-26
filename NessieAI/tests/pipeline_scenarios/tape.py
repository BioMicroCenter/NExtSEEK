"""Record, then replay, every answer the pipeline tools get from outside the code under test.

Recording wraps the I/O functions in TAPED and the model calls; replay patches the same names
to hand the recorded answers back, in order, per source, and refuses a call the tape did not
see or one with other arguments (TapeMismatch), which is the signal to re-record. The digest
and the ~100k-token selection payload are not kept: the selection model's recorded answer is
replayed instead, and the payload's sha256 is kept for the record.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib
import json
from types import SimpleNamespace
from unittest import mock

#: The functions through which the pipeline tools read the world. A replay that hits the
#: network means one is missing here: add it, re-record, and say so in the commit.
TAPED: tuple[str, ...] = (
    "chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata",
    "chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes",
    "chat_nextseek.pipeline.agent_tools.gather_protocol_text",
    "chat_nextseek.pipeline.agent_tools.expand_uid_prefixes",
    "chat_nextseek.pipeline.agent_tools.lookup_run_sizes",
    "chat_nextseek.pipeline.agent_tools.resolve_accessions",
)


#: Sources replayed by order only. Their arguments legitimately differ between recording and
#: replay: the agent's messages carry temporary log paths, and the metadata handed on to
#: annotate and to the protocol reader is the scrubbed copy in replay.
UNKEYED = frozenset({"agent", "annotate_metadata_with_sampletypes", "gather_protocol_text"})


class TapeMismatch(AssertionError):
    """The code asked something the tape does not hold: re-record with a paid run."""


def encode(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        return {"__dataclass__": f"{cls.__module__}.{cls.__qualname__}",
                "fields": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"cannot tape a {type(value).__name__}")


def decode(value):
    if isinstance(value, dict) and "__dataclass__" in value:
        module, _, name = value["__dataclass__"].rpartition(".")
        cls = importlib.import_module(module)
        for part in name.split("."):
            cls = getattr(cls, part)
        return cls(**{k: decode(v) for k, v in value["fields"].items()})
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def _args_key(args, kwargs) -> str:
    """A key that ignores which object was passed (a live config or a replay one)."""
    def plain(v):
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, (list, tuple, set)):
            return [plain(x) for x in (sorted(v) if isinstance(v, set) else v)]
        if isinstance(v, dict):
            return {str(k): plain(x) for k, x in v.items()}
        return "<object>"
    blob = json.dumps([plain(list(args)), plain(kwargs)], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class Tape:
    def __init__(self, entries: dict | None = None, mode: str = "record"):
        self.entries: dict[str, list[dict]] = entries or {}
        self.mode = mode
        self._cursor: dict[str, int] = {}

    def record(self, source: str, args, kwargs, value) -> None:
        self.entries.setdefault(source, []).append({"args": _args_key(args, kwargs), "value": encode(value)})

    def play(self, source: str, args, kwargs):
        i = self._cursor.get(source, 0)
        queue = self.entries.get(source) or []
        if i >= len(queue):
            raise TapeMismatch(f"{source} was called more often than recorded ({len(queue)} times)")
        self._cursor[source] = i + 1
        if queue[i]["args"] != _args_key(args, kwargs):
            raise TapeMismatch(f"{source} call {i + 1} came with other arguments than recorded")
        return decode(queue[i]["value"])

    def to_json(self) -> dict:
        return {"entries": self.entries}

    @classmethod
    def from_json(cls, data: dict) -> "Tape":
        return cls(entries=data.get("entries") or {}, mode="replay")


class RecordingClient:
    """Passes every call through and keeps the selection model's text answer."""
    def __init__(self, client, tape: Tape):
        self._client, self._tape = client, tape

    def chat(self, **kw):
        resp = self._client.chat(**kw)
        self._tape.record("selection", (), {}, getattr(resp, "content", None) or "")
        return resp

    def chat_with_tools(self, **kw):
        return self._client.chat_with_tools(**kw)

    def __getattr__(self, name):
        return getattr(self._client, name)


class ReplayClient:
    def __init__(self, tape: Tape):
        self._tape = tape

    def chat(self, **kw):
        return SimpleNamespace(content=self._tape.play("selection", (), {}))

    def chat_with_tools(self, **kw):
        raise TapeMismatch("chat_with_tools reached the client; the agent's calls replay through call_tools")


class _PayloadStub:
    def __init__(self, sha: str):
        self.sha = sha

    def to_prompt_text(self, *a, **k) -> str:
        return f"<replayed selection payload sha256={self.sha}>"

    def size_report(self, *a, **k) -> dict:
        return {}


def _resolve(target: str):
    module, _, attr = target.rpartition(".")
    return importlib.import_module(module), attr


@contextlib.contextmanager
def taped(tape: Tape, scrubber=None):
    """Record (tape.mode == "record") or replay every outside answer the pipeline tools get.

    ``scrubber(source, value) -> value`` runs on each recorded value before it is kept.
    """
    from chat_nextseek.pipeline import agent, agent_tools

    patches = []

    def wrap(source, original):
        def key(args, kwargs):
            return ((), {}) if source in UNKEYED else (args, kwargs)

        def recording(*args, **kwargs):
            value = original(*args, **kwargs)
            tape.record(source, *key(args, kwargs), scrubber(source, value) if scrubber else value)
            return value

        def replaying(*args, **kwargs):
            return tape.play(source, *key(args, kwargs))
        return recording if tape.mode == "record" else replaying

    for target in TAPED:
        module, attr = _resolve(target)
        patches.append(mock.patch.object(module, attr, wrap(attr, getattr(module, attr))))
    patches.append(mock.patch.object(agent, "call_tools", wrap("agent", agent.call_tools)))

    if tape.mode == "record":
        real_ctx = agent_tools.build_selection_context

        def recording_ctx(*args, **kwargs):
            ctx = real_ctx(*args, **kwargs)
            text = ctx.to_prompt_text(*(a for a in [kwargs.get("sections")] if a))
            tape.record("payload", (), {}, hashlib.sha256(text.encode()).hexdigest())
            return ctx
        patches.append(mock.patch.object(agent_tools, "build_selection_context", recording_ctx))
    else:
        patches.append(mock.patch.object(agent_tools, "build_sample_digest", lambda *a, **k: {}))
        patches.append(mock.patch.object(
            agent_tools, "build_selection_context",
            lambda *a, **k: _PayloadStub(tape.play("payload", (), {}))))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield tape
