"""One definition of the JSON envelope the AJAX views return.

The same three-key dict used to be spelled out 27 times across ``views.py`` and
``decorators.py``. The shape is a contract with the JavaScript under
``templates/``: ``msg`` is shown to the user, ``status`` is tested for truth, and
``link`` is a download URL or the empty string.

``simplejson`` and ``default=str`` are both deliberate and must not be swapped
for the stdlib ``json``. The two disagree on ``Decimal`` and ``NaN``, and
``default=str`` is what lets a date serialise instead of raising.

Key order is part of what this module pins down: ``simplejson`` writes a dict in
insertion order, and every pre-existing response ordered the keys ``msg``,
``status``, ``link``, with ``message`` last where it appears at all.

``seek/dbtable_sample.py`` deliberately does **not** use this. It returns JSON
strings rather than responses, assembles the dict incrementally, and emits the
keys in a different order -- in one case adding them to a payload that already
carries ``rows``. Routing it through here would reorder its keys.
"""

import simplejson
from django.http import HttpResponse


def json_envelope(msg, status, link='', message=None):
    """The response dict, for callers that add to it before returning."""
    data = {'msg': msg, 'status': status, 'link': link}
    if message is not None:
        data['message'] = message
    return data


def json_response(msg, status, link='', message=None):
    """The envelope, serialised and wrapped in an ``HttpResponse``."""
    return HttpResponse(
        simplejson.dumps(json_envelope(msg, status, link, message), default=str))


def plain_text(message):
    """Flatten the ``<br/>``-marked-up feedback message into newline-separated text.

    Four views repeated this as the same four-line if/else. ``message`` may be
    ``None``, which the original guarded for and passed through untouched.
    """
    if message is not None and '<br/>' in message:
        return message.replace('<br/>', '\n')
    return message
