"""The op slots and the chat's bundle ids under real concurrency (approach 1, piece 2; spec rev 4).

Lane M only (``lane_mysql``): a throwaway MySQL 8.0, and every worker a thread with its own connection, so the
conditional UPDATE and the row locks are MySQL's own, not SQLite's single writer. Anywhere else the ``mysql_lane``
marker skips these tests (NessieAI/tests/ns/conftest.py).
"""
from __future__ import annotations

import threading

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from NessieAI.ns.turn_memory import MAX_OPS_IN_FLIGHT, release_op_slot, take_op_slot
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

pytestmark = pytest.mark.mysql_lane

WORKERS = 8


def race(n, fn):
    """Run ``fn(i)`` on ``n`` threads that start together, each on its own DB connection; their results in order."""
    barrier = threading.Barrier(n)
    results, conn_ids, errors = [None] * n, [None] * n, []

    def worker(i):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT CONNECTION_ID()")
                conn_ids[i] = cur.fetchone()[0]
            barrier.wait(30)
            results[i] = fn(i)
        except BaseException as exc:  # noqa: BLE001 - asserted below, on the test's thread
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads), "a worker hung"
    assert errors == [], errors
    assert len(set(conn_ids)) == n, conn_ids  # every worker on a connection of its own
    return results


class OpSlotContentionTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        self.assertEqual(connection.vendor, "mysql")
        user = User.objects.create_user("u1", password="p")
        chat = ChatSession.objects.create(user=user)
        task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
        self.turn = CCTurn.objects.create(task=task, user=user, chat=chat, pass_hash="a" * 64)

    def in_flight(self) -> int:
        self.turn.refresh_from_db(fields=["ops_in_flight"])
        return self.turn.ops_in_flight

    def test_exactly_two_of_eight_racing_ops_take_a_slot(self):
        taken = race(WORKERS, lambda i: take_op_slot(self.turn))
        self.assertEqual(taken.count(True), MAX_OPS_IN_FLIGHT)
        self.assertEqual(taken.count(False), WORKERS - MAX_OPS_IN_FLIGHT)
        self.assertEqual(self.in_flight(), MAX_OPS_IN_FLIGHT)

    def test_racing_releases_restore_the_slots_and_never_go_below_zero(self):
        take_op_slot(self.turn)
        take_op_slot(self.turn)
        race(WORKERS, lambda i: release_op_slot(self.turn))  # eight releases for two slots
        self.assertEqual(self.in_flight(), 0)
        taken = race(WORKERS, lambda i: take_op_slot(self.turn))
        self.assertEqual(taken.count(True), MAX_OPS_IN_FLIGHT)

    def test_takes_and_releases_under_load_never_hold_more_than_two(self):
        """Each worker takes, and gives back what it took, twenty times. A holder is counted after its take returned
        True and uncounted before its release, so the count can only undercount what the row holds."""
        lock = threading.Lock()
        holders, peak = [0], [0]

        def worker(i):
            got = 0
            for _ in range(20):
                if not take_op_slot(self.turn):
                    continue
                got += 1
                with lock:
                    holders[0] += 1
                    peak[0] = max(peak[0], holders[0])
                with lock:
                    holders[0] -= 1
                release_op_slot(self.turn)
            return got

        got = race(WORKERS, worker)
        self.assertGreater(sum(got), 0)
        self.assertLessEqual(peak[0], MAX_OPS_IN_FLIGHT)
        self.assertEqual(self.in_flight(), 0)
