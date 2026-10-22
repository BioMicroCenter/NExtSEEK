"""Two real connections reserving from one prefix never share an index.

Needs a MySQL/MariaDB default database (row locks); skipped on the lane's SQLite. Unrun until the MariaDB CI lane exists.
"""
import threading

import pytest
from django.conf import settings
from django.db import connections

from nextseek_api.batch_upload.uid_sequence import reserve_uid_indexes

pytestmark = [
    pytest.mark.host_only,
    pytest.mark.skipif(
        "mysql" not in settings.DATABASES["default"]["ENGINE"],
        reason="needs a MySQL/MariaDB default database for SELECT ... FOR UPDATE",
    ),
]


@pytest.mark.django_db(transaction=True)
def test_concurrent_reservations_do_not_overlap():
    starts, barrier = [], threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            starts.append(reserve_uid_indexes("CONC-261014MIT", 5, 7))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(starts) == [8, 13]
