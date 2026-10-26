"""Record, then replay, every answer the pipeline tools get from outside the code under test.

Recording wraps the I/O functions in TAPED and the model calls; replay patches the same names
to hand the recorded answers back, in order, per source, and refuses a call the tape did not
see or one with other arguments (TapeMismatch), which is the signal to re-record. The digest
and the ~100k-token selection payload are not kept: the selection model's recorded answer is
replayed instead, and the payload's sha256 is kept for the record. A step that raised (the
digest, the payload, the selection call) is taped as {"raise": "<module>.<Class>", "message":
...} and raised again in replay, so a run that ended selection early replays to the same end.
"""
from __future__ import annotations

import concurrent.futures
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


def exception_spec(exc: BaseException, **extra) -> dict:
    cls = type(exc)
    return {"raise": f"{cls.__module__}.{cls.__qualname__}", "message": str(exc), **extra}


def is_raise(value) -> bool:
    return isinstance(value, dict) and "raise" in value and "message" in value


def rebuild_exception(spec: dict) -> BaseException:
    """The recorded exception: its class imported by dotted name and given the message, or a
    RuntimeError naming both when the class is gone or cannot take one message argument."""
    name, message = spec["raise"], spec["message"]
    try:
        module, _, qualname = name.rpartition(".")
        cls = importlib.import_module(module)
        for part in qualname.split("."):
            cls = getattr(cls, part)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            return cls(message)
    except Exception:  # noqa: BLE001 - any failure to rebuild falls through to RuntimeError
        pass
    return RuntimeError(f"{name}: {message}")


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
            return [plain(x) for x in (sorted(v, key=repr) if isinstance(v, set) else v)]
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
        #: Every refused replay call, kept even when the caller swallows the TapeMismatch (the
        #: agent loop, selection.decide and tool_select_pipeline all catch exceptions).
        self.mismatches: list[str] = []
        #: Every refusal a recording scrubber raised, kept for the same reason.
        self.refused: list[str] = []
        self._final: set[int] = set()
        self._source_of: dict[int, str] = {}

    def record(self, source: str, args, kwargs, value) -> None:
        self.entries.setdefault(source, []).append({"args": _args_key(args, kwargs), "value": encode(value)})

    def play(self, source: str, args, kwargs):
        i = self._cursor.get(source, 0)
        queue = self.entries.get(source) or []
        if i >= len(queue):
            return self._mismatch(f"{source} was called more often than recorded ({len(queue)} times)")
        self._cursor[source] = i + 1
        if queue[i]["args"] != _args_key(args, kwargs):
            return self._mismatch(f"{source} call {i + 1} came with other arguments than recorded")
        return decode(queue[i]["value"])

    def peek(self, source: str):
        """The next entry's value, undecoded and not consumed; None when there is none."""
        queue = self.entries.get(source) or []
        i = self._cursor.get(source, 0)
        return queue[i]["value"] if i < len(queue) else None

    def reserve(self, source: str, args=(), kwargs=None) -> dict:
        """Take the next slot of a source now and fill it when the call ends, so entries stay in
        call order even when a call runs on a worker thread the tool may give up waiting for."""
        entry = {"args": _args_key(args, kwargs or {}), "value": None}
        self.entries.setdefault(source, []).append(entry)
        self._source_of[id(entry)] = source
        return entry

    def fill(self, entry: dict, value, final: bool = False) -> None:
        if id(entry) in self._final:
            return          # the tool already gave up on this call and its timeout was taped
        entry["value"] = encode(value)
        # These entries skip the run-time scrubber, so an exception message that carries a path,
        # an email or a private URL is caught here and makes taped() raise at exit.
        from . import scrub
        hits = scrub.refusals_in(entry["value"])
        if hits:
            self.refused.append(f"{self._source_of.get(id(entry), '?')}: {'; '.join(hits)}")
        if final:
            self._final.add(id(entry))

    def mark_timeout(self, source: str) -> None:
        """The tool stopped waiting for a call of this source: tape that, not the late answer."""
        queue = self.entries.get(source)
        entry = queue[-1] if queue else self.reserve(source)
        self.fill(entry, exception_spec(TimeoutError(f"{source} timed out"), waiting=True), final=True)

    def _mismatch(self, message: str):
        self.mismatches.append(message)
        raise TapeMismatch(message)

    def unplayed(self) -> dict[str, int]:
        """Source -> how many recorded entries were never played."""
        left = {source: len(queue) - self._cursor.get(source, 0) for source, queue in self.entries.items()}
        return {source: n for source, n in left.items() if n > 0}

    def to_json(self) -> dict:
        return {"entries": self.entries}

    @classmethod
    def from_json(cls, data: dict) -> "Tape":
        return cls(entries=data.get("entries") or {}, mode="replay")


class RecordingClient:
    """Passes every call through and keeps the selection model's text answer, or its failure."""
    def __init__(self, client, tape: Tape):
        self._client, self._tape = client, tape

    def chat(self, **kw):
        entry = self._tape.reserve("selection")
        try:
            resp = self._client.chat(**kw)
        except Exception as exc:
            self._tape.fill(entry, exception_spec(exc))
            raise
        self._tape.fill(entry, getattr(resp, "content", None) or "")
        return resp

    def chat_with_tools(self, **kw):
        return self._client.chat_with_tools(**kw)

    def __getattr__(self, name):
        return getattr(self._client, name)


class ReplayClient:
    def __init__(self, tape: Tape):
        self._tape = tape

    def chat(self, **kw):
        answer = self._tape.play("selection", (), {})
        if is_raise(answer):
            raise rebuild_exception(answer)
        return SimpleNamespace(content=answer)

    def chat_with_tools(self, **kw):
        raise TapeMismatch("chat_with_tools reached the client; the agent's calls replay through call_tools")


class _PayloadStub:
    def __init__(self, sha: str):
        self.sha = sha

    def to_prompt_text(self, *a, **k) -> str:
        return f"<replayed selection payload sha256={self.sha}>"

    def size_report(self, *a, **k) -> dict:
        return {}


class _HashingContext:
    """Wraps the real selection context while recording: the sha256 of the text the tool itself
    asks for (its own to_prompt_text call, whatever sections it passes) is kept on the tape, or
    the failure that call raised."""
    def __init__(self, ctx, tape: Tape, entry: dict):
        self._ctx, self._tape, self._entry = ctx, tape, entry

    def to_prompt_text(self, *args, **kwargs):
        try:
            text = self._ctx.to_prompt_text(*args, **kwargs)
        except Exception as exc:
            self._tape.fill(self._entry, exception_spec(exc))
            raise
        self._tape.fill(self._entry, hashlib.sha256(text.encode()).hexdigest())
        return text

    def __getattr__(self, name):
        return getattr(self._ctx, name)


class _ObservedFuture:
    def __init__(self, future, source, tape: Tape):
        self._future, self._source, self._tape = future, source, tape

    def result(self, timeout=None):
        try:
            return self._future.result(timeout)
        except concurrent.futures.TimeoutError:
            # Only the tool giving up on a call that is still running counts; a TimeoutError the
            # call itself raised comes with a finished future and is taped by the wrapper.
            if self._source and not self._future.done():
                self._tape.mark_timeout(self._source)
            raise

    def __getattr__(self, name):
        return getattr(self._future, name)


class _ObservedPool:
    """The tools bound a worker with future.result(timeout=...); this notices when it gives up."""
    def __init__(self, tape: Tape, *args, **kwargs):
        self._pool = concurrent.futures.ThreadPoolExecutor(*args, **kwargs)
        self._tape = tape

    def submit(self, fn, *args, **kwargs):
        return _ObservedFuture(self._pool.submit(fn, *args, **kwargs),
                               getattr(fn, "_tape_source", None), self._tape)

    def __getattr__(self, name):
        return getattr(self._pool, name)


def _resolve(target: str):
    module, _, attr = target.rpartition(".")
    return importlib.import_module(module), attr


@contextlib.contextmanager
def taped(tape: Tape, scrubber=None):
    """Record (tape.mode == "record") or replay every outside answer the pipeline tools get.

    ``scrubber(source, value) -> value`` runs on each recorded value before it is kept, and the
    scrubbed value is what the live run gets back, so recording and replay see the same data.

    The tools' callers catch exceptions, so a TapeMismatch or a scrubber's refusal never stops
    a run by itself. They are collected on the tape and raised here, at exit: a replay that
    asked for something unrecorded, or left a recorded entry unplayed, raises TapeMismatch; a
    recording whose scrubber refused raises ValueError.
    """
    from chat_nextseek.pipeline import agent, agent_tools

    patches = []

    def wrap(source, original):
        def key(args, kwargs):
            return ((), {}) if source in UNKEYED else (args, kwargs)

        def recording(*args, **kwargs):
            value = original(*args, **kwargs)
            try:
                kept = scrubber(source, value) if scrubber else value
                tape.record(source, *key(args, kwargs), kept)
            except Exception as exc:
                tape.refused.append(f"{source}: {type(exc).__name__}: {exc}")
                raise
            return decode(encode(kept))

        def replaying(*args, **kwargs):
            return tape.play(source, *key(args, kwargs))
        return recording if tape.mode == "record" else replaying

    for target in TAPED:
        module, attr = _resolve(target)
        patches.append(mock.patch.object(module, attr, wrap(attr, getattr(module, attr))))
    patches.append(mock.patch.object(agent, "call_tools", wrap("agent", agent.call_tools)))

    real_digest, real_decide = agent_tools.build_sample_digest, agent_tools.selection.decide
    real_ctx = agent_tools.build_selection_context
    if tape.mode == "record":
        def digest_recording(*args, **kwargs):
            entry = tape.reserve("digest")
            try:
                out = real_digest(*args, **kwargs)
            except Exception as exc:
                tape.fill(entry, exception_spec(exc))
                raise
            tape.fill(entry, {"ok": True})
            return out
        digest_recording._tape_source = "digest"

        def decide_recording(*args, **kwargs):
            return real_decide(*args, **kwargs)
        decide_recording._tape_source = "selection"

        def ctx_recording(*args, **kwargs):
            entry = tape.reserve("payload")
            try:
                ctx = real_ctx(*args, **kwargs)
            except Exception as exc:
                tape.fill(entry, exception_spec(exc))
                raise
            tape.fill(entry, "")
            return _HashingContext(ctx, tape, entry)
        digest_stub, decide_stub, ctx_stub = digest_recording, decide_recording, ctx_recording
    else:
        def digest_replaying(*args, **kwargs):
            answer = tape.play("digest", (), {})
            if is_raise(answer):
                raise rebuild_exception(answer)
            return {}

        def decide_replaying(*args, **kwargs):
            answer = tape.peek("selection")
            if is_raise(answer) and answer.get("waiting"):
                tape.play("selection", (), {})      # the tool waited for the model and gave up
                raise TimeoutError(answer["message"])
            return real_decide(*args, **kwargs)

        def ctx_replaying(*args, **kwargs):
            answer = tape.play("payload", (), {})
            if is_raise(answer):
                raise rebuild_exception(answer)
            return _PayloadStub(answer)
        digest_stub, decide_stub, ctx_stub = digest_replaying, decide_replaying, ctx_replaying
    patches.append(mock.patch.object(agent_tools, "build_sample_digest", digest_stub))
    patches.append(mock.patch.object(agent_tools.selection, "decide", decide_stub))
    patches.append(mock.patch.object(agent_tools, "build_selection_context", ctx_stub))
    if tape.mode == "record":
        # A stand-in for the concurrent module: `with ThreadPoolExecutor() as pool:` would not
        # work through it (no __enter__), and the tools deliberately never use that form.
        observed = SimpleNamespace(futures=SimpleNamespace(
            ThreadPoolExecutor=lambda *a, **k: _ObservedPool(tape, *a, **k),
            TimeoutError=concurrent.futures.TimeoutError))
        patches.append(mock.patch.object(agent_tools, "concurrent", observed))
    try:
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            yield tape
    except BaseException as exc:
        _raise_collected(tape, exc)
        raise
    _raise_collected(tape, None)
    if tape.mode != "record" and tape.unplayed():
        raise TapeMismatch(f"recorded entries were never played (re-record): {tape.unplayed()}")


def _raise_collected(tape: Tape, cause) -> None:
    if tape.mode == "record":
        if tape.refused:
            raise ValueError("the tape was refused: " + "; ".join(tape.refused)) from cause
    elif tape.mismatches:
        raise TapeMismatch("the replay did not match the tape: " + "; ".join(tape.mismatches)) from cause
