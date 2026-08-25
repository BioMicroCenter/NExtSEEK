"""Authentication preambles shared by the views in :mod:`seek.views`.

Before this module existed, eleven views in ``views.py`` opened with the same
block -- construct a ``SeekDB``, call ``getSeekLogin``, and on failure assemble
a JSON error envelope and return it -- and twelve repeated an equivalent block
for the supervisor check. The decorators here hold those two blocks once.

The envelopes they emit are byte-for-byte what the inlined copies emitted.
That includes key order, which ``simplejson`` takes from dict insertion order,
and it includes which failures are logged: three views logged the login failure
and the other eight did not, so ``log_failure`` is a per-view flag rather than a
uniform improvement. The supervisor message is likewise per-view. Unifying
either the wording or the logging is a user-visible change and belongs to the
error-presentation step, not here.
"""

import functools
import logging

import simplejson
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseRedirect

from .seekdb import SeekDB

logger = logging.getLogger(__name__)


def _login(request, whetherFullInfo):
    """Log a request in to SEEK and hand the view what the login produced.

    ``getSeekLogin`` rebinds ``self.user_seek``, ``self.creator`` and -- the one
    that matters -- ``self.__seekapi``, which it rebuilds with the caller's
    credentials. Every later SEEK call therefore has to go through *this*
    instance: a view that constructed its own ``SeekDB`` would get an
    unauthenticated API client and quietly read nothing. That is why the
    instance is attached as ``request.seekdb`` rather than left for the view to
    recreate.
    """
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, whetherFullInfo)
    request.seekdb = seekdb
    request.user_seek = user_seek
    return user_seek


def verifySuperUser(request):
    user = request.user
    if user.is_authenticated:
        try:
            if user.is_superuser:
                return 1
            return 0
        except User.DoesNotExist:
            return 0
    return 0


def _error_response(msg, with_message_key=False):
    """The JSON envelope every auth failure in ``views.py`` returns.

    ``with_message_key`` reproduces the two page views that also send an empty
    ``message`` field; every other caller omits it.
    """
    data = {'msg': msg, 'status': 0, 'link': ''}
    if with_message_key:
        data['message'] = ''
    return HttpResponse(simplejson.dumps(data, default=str))


def requires_seek_login(view=None, *, log_failure=False):
    """Reject the request with a JSON error unless the SEEK login succeeds.

    Use as ``@requires_seek_login`` or ``@requires_seek_login(log_failure=True)``.
    On success the login context is attached as ``request.user_seek``, which is
    how the three views that read it get hold of it without a second
    ``getSeekLogin`` round-trip.
    """
    if view is None:
        return functools.partial(requires_seek_login, log_failure=log_failure)

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        user_seek = _login(request, False)
        if not user_seek['status']:
            err = user_seek['err']
            if log_failure:
                logger.error(err)
            return _error_response(err)
        return view(request, *args, **kwargs)

    return wrapper


def requires_seek_login_redirect(next_url=None, *, whetherFullInfo=False):
    """Send the browser to the login page unless the SEEK login succeeds.

    The page views use this where the AJAX views use :func:`requires_seek_login`:
    a redirect rather than a JSON envelope. ``next_url`` is the path to return to
    afterwards; ``None`` reproduces the four views that redirect to a bare
    ``/login/`` with no ``next`` parameter at all. The target is assembled once,
    at decoration time, by the same concatenation the inlined copies used.

    ``whetherFullInfo`` is ``getSeekLogin``'s own second argument. Two views pass
    ``True`` and the rest ``False``; it is a parameter rather than a constant
    because that difference is real and undocumented.
    """
    target = '/login/' if next_url is None else '/login/?next=' + next_url

    def decorate(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not _login(request, whetherFullInfo)['status']:
                return HttpResponseRedirect(target)
            return view(request, *args, **kwargs)
        return wrapper
    return decorate


def requires_supervisor(message, with_message_key=False):
    """Reject the request with ``message`` unless the user is a superuser.

    ``message`` is per-view because the inlined copies worded it differently per
    action -- and one of them names the wrong noun (``LATENT_BUGS.md`` #42).
    Both are passed through verbatim.
    """
    def decorate(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if verifySuperUser(request) != 1:
                logger.error(message)
                return _error_response(message, with_message_key)
            return view(request, *args, **kwargs)
        return wrapper
    return decorate
