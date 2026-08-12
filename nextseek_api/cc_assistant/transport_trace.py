"""Provider-transport call tracing for V4-6 call-count contract."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import local

__all__ = [
    "TransportTrace",
    "get_active_trace",
    "install_transport_hooks",
    "reset_transport_hooks",
    "trace_context",
]

_STATE = local()
_HOOKS_INSTALLED = False


@dataclass
class TransportTrace:
    classify_calls: int = 0
    route_calls: int = 0
    events: list[str] = field(default_factory=list)

    def record_classify(self, detail: str = "classify") -> None:
        self.classify_calls += 1
        self.events.append(detail)

    def record_route(self, detail: str = "route") -> None:
        self.route_calls += 1
        self.events.append(detail)


def get_active_trace() -> TransportTrace | None:
    return getattr(_STATE, "trace", None)


@contextmanager
def trace_context():
    trace = TransportTrace()
    previous = getattr(_STATE, "trace", None)
    _STATE.trace = trace
    try:
        yield trace
    finally:
        _STATE.trace = previous


def install_transport_hooks(b_module: object) -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return

    original_classify = getattr(b_module, "ClassifyQuery", None)
    original_route = getattr(b_module, "RouteQuery", None)
    if original_classify is None or original_route is None:
        return

    async def _wrap_classify(*args, **kwargs):
        trace = get_active_trace()
        if trace is not None:
            trace.record_classify("ClassifyQuery")
        return await original_classify(*args, **kwargs)

    async def _wrap_route(*args, **kwargs):
        trace = get_active_trace()
        if trace is not None:
            trace.record_route("RouteQuery")
        return await original_route(*args, **kwargs)

    setattr(b_module, "ClassifyQuery", _wrap_classify)
    setattr(b_module, "RouteQuery", _wrap_route)
    _HOOKS_INSTALLED = True


def reset_transport_hooks() -> None:
    global _HOOKS_INSTALLED
    _HOOKS_INSTALLED = False
