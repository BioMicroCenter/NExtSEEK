"""Django models for the ``seek`` app, split by who owns the table.

* ``seek_mirror`` -- 19 mirrors of upstream SEEK's Rails tables, routed to the
  ``seek`` database alias
* ``nextseek``    -- 8 tables NExtSEEK itself defines and writes

The star re-exports keep every existing ``from .models import X`` working, and
Django's registry is untouched because it identifies a model by ``app_label`` +
class name, not by declaring module. Import order is irrelevant: no model here
references another model class directly -- every relation uses a string
reference. The migrations in ``seek/migrations/`` do not import this package.
"""

from .seek_mirror import *  # noqa: F401,F403
from .nextseek import *  # noqa: F401,F403
