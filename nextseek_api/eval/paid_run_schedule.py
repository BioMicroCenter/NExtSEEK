"""Default-off schedule entrypoints for paid eval runs (V4-8)."""
from __future__ import annotations

from nextseek_api.eval.run_authorization import AuthorizationError

__all__ = ["ScheduleRefused", "default_schedule_entrypoint"]


class ScheduleRefused(AuthorizationError):
    pass


def default_schedule_entrypoint(*, enabled: bool = False) -> None:
    """Celery beat / default schedule must not enter paid lane without manifest."""
    if not enabled:
        raise ScheduleRefused("default schedule disabled for paid lane")
    raise ScheduleRefused("schedule entry requires approved manifest hash")
