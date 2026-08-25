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
from django.http import HttpResponse

from .seekdb import SeekDB

logger = logging.getLogger(__name__)


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
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request, False)
        if not user_seek['status']:
            err = user_seek['err']
            if log_failure:
                logger.error(err)
            return _error_response(err)
        request.user_seek = user_seek
        return view(request, *args, **kwargs)

    return wrapper


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
