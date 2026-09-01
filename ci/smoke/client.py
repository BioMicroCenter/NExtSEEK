"""The profile guard.

Enforcement lives here, in the client, and not in the tests. A rule a test
author has to remember is a rule that eventually gets forgotten, and the
failure mode of forgetting this one is a write against production.
"""
from __future__ import annotations

import requests

from ci import routes


class ProfileViolation(RuntimeError):
    """Raised BEFORE a request leaves the process."""


class GuardedSession(requests.Session):
    def __init__(self, profile: str, **kwargs) -> None:
        super().__init__()
        self.profile = profile
        for k, v in kwargs.items():
            setattr(self, k, v)

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        # Three checks, in this order, and no others. The guard deliberately reads
        # neither route.auth nor route.methods: `auth` records which client the sweep
        # uses and `methods` records what CI sends today, while a negative test
        # legitimately calls a route with a weaker account than the entry declares, so
        # that the application's own permission classes are what answers. Turning
        # either field into an assertion here would refuse those tests, and is a
        # decision to take deliberately rather than a line to add in passing.
        route = routes.match(url)
        if route is None:
            raise ProfileViolation(
                f"unregistered URL: {url}\n"
                f"Declare it in ci/routes.py, or the guard cannot know whether it is safe."
            )
        if self.profile not in route.profiles:
            reason = f" ({route.exclude})" if route.exclude else ""
            enabled = ", ".join(sorted(route.profiles))
            where = (
                f"It is enabled for: {enabled}."
                if enabled
                else "It is enabled for no profile at all."
            )
            raise ProfileViolation(
                f"{url} is not enabled for profile {self.profile!r}{reason}. {where}"
            )
        # The prod profile is read-only, with one exception a route must opt into by
        # declaring prod_allows_non_get: a sweep that has to authenticate cannot log
        # in with a GET.
        if (
            self.profile == "prod"
            and method.upper() != "GET"
            and not route.prod_allows_non_get
        ):
            raise ProfileViolation(
                f"{method.upper()} refused under the prod profile: {url}\n"
                f"The prod sweep is read-only. Run this against the local or dev "
                f"profile instead."
            )
        return super().request(method, url, *args, **kwargs)
