"""The profile guard.

Enforcement lives here, in the client, and not in the tests. A rule a test
author has to remember is a rule that eventually gets forgotten, and the
failure mode of forgetting this one is a write against production.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import requests

from ci import routes


class ProfileViolation(RuntimeError):
    """Raised BEFORE a request leaves the process."""


class GuardedSession(requests.Session):
    """A requests.Session that refuses a URL the registry does not permit.

    `base_url`, when given, binds the session to one instance: every URL it is
    asked for, redirect hops included, must name that scheme, host and port.
    """

    def __init__(self, profile: str, base_url: str | None = None, **kwargs) -> None:
        super().__init__()
        self.profile = profile
        self.base_url = base_url
        for k, v in kwargs.items():
            # requests.Session's own settable state -- auth, headers, verify -- is
            # instance state, so a kwarg naming a CALLABLE on the class is naming a
            # method. Allowing that would let a caller pass request= or send= and
            # replace the guard with a lambda.
            if callable(getattr(type(self), k, None)):
                raise TypeError(
                    f"{k!r} is a method of {type(self).__name__}; setting it through "
                    f"the constructor would replace the guard rather than configure it"
                )
            setattr(self, k, v)

    def _check(self, method, url) -> None:
        """Refuse, or return. The only enforcement in this file.

        Called from both request() and send(): requests follows a redirect by
        re-entering send() alone, so a check that lived only in request() would
        wave through every hop after the first.
        """
        # Membership, profile and the prod non-GET rule are the whole of the guard.
        # It deliberately does not check route.auth or route.methods: `auth` records
        # which client the sweep uses and `methods` records what CI sends today, while
        # a negative test legitimately calls a route with a weaker account than the
        # entry declares, so that the application's own permission classes are what
        # answers. Turning either field into an assertion here would refuse those
        # tests, and is a decision to take deliberately rather than a line to add in
        # passing.
        if self.base_url:
            here = urlsplit(self.base_url)
            there = urlsplit(url)
            # An explicitly written default port does not compare equal to an omitted
            # one, so http://h and http://h:80 are treated as different instances.
            # That direction of inexactness refuses; the other would forward.
            if (there.scheme, there.hostname, there.port) != (
                here.scheme, here.hostname, here.port
            ):
                raise ProfileViolation(
                    f"off-instance URL refused: {url}\n"
                    f"This session is bound to {self.base_url}. A redirect or a "
                    f"hard-coded host has taken the request somewhere else."
                )
        route = routes.match(url)
        if route is None:
            raise ProfileViolation(
                f"unregistered URL: {url}\n"
                "Declare it in ci/routes.py, or the guard cannot know whether it is safe."
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
        verb = (method or "").upper()
        if self.profile == "prod" and verb != "GET" and not route.prod_allows_non_get:
            raise ProfileViolation(
                f"{verb} refused under the prod profile: {url}\n"
                "The prod sweep is read-only. Run this against the local or dev "
                "profile instead."
            )

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        # Checked here as well as in send() so that the traceback points at the line
        # that asked for the URL, rather than at the machinery underneath it.
        self._check(method, url)
        return super().request(method, url, *args, **kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        # Every byte leaves through here: Session.request's own dispatch, a caller
        # holding a PreparedRequest, and each hop resolve_redirects follows.
        self._check(request.method, request.url)
        return super().send(request, **kwargs)
