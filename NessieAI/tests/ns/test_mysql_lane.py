"""The op slots and the chat's bundle ids under real concurrency (approach 1, piece 2; spec rev 4).

Lane M only (``lane_mysql``): a throwaway MySQL 8.0, and every worker a thread with its own connection, so the
conditional UPDATE and the row locks are MySQL's own, not SQLite's single writer. Anywhere else the ``mysql_lane``
marker skips these tests (NessieAI/tests/ns/conftest.py).
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from chat_nextseek.orchestrator import _next_bundle_id
from NessieAI.ns import granular
from NessieAI.ns.turn_memory import MAX_OPS_IN_FLIGHT, release_op_slot, take_op_slot
from nextseek_api.assistant import bundle_ids
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter

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


BASE = "/nextseek_api/assistant"


def _report_handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
    path = Path(outputs_dir) / f"summary-{threading.get_ident()}.json"
    path.write_text("{}")
    return {"summary": {}, "saved_files": {"summary_json": str(path)}, "rows": {}}


class BundleIdContentionTests(TransactionTestCase):
    """Review focus 3 on a real database: one allocator, many writers at once, each on its own connection."""

    databases = {"default"}

    def setUp(self):
        self.assertEqual(connection.vendor, "mysql")
        self.user = User.objects.create_user("u1", password="p")
        self.chat = ChatSession.objects.create(user=self.user)
        outputs = tempfile.mkdtemp()
        for patcher in (
            patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True),
            patch("nextseek_api.services.assistant._granular_chat_config", return_value=SimpleNamespace()),
            patch("nextseek_api.services.assistant._granular_outputs_dir", return_value=outputs),
            patch.dict(granular._HANDLERS, {"report": _report_handler}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_racing_allocations_hand_out_distinct_ids(self):
        ids = race(WORKERS, lambda i: bundle_ids.allocate_bundle_id(self.chat.pk))
        self.assertEqual(sorted(ids), list(range(1, WORKERS + 1)))
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.extra_state[bundle_ids.BUNDLE_SEQ_KEY], WORKERS)

    def test_racing_ns_turns_and_report_ops_keep_every_bundle(self):
        """Half the workers are NS turns (load the chat, number a bundle, append it, save), half are report ops (the
        artifact bundle under the row lock). No id is handed out twice and the merge loses no bundle."""
        def worker(i):
            if i % 2:
                client = APIClient()
                client.force_authenticate(user=self.user)
                resp = client.post(f"{BASE}/report/", {"mode": "samples", "project": "p",
                                                        "session_id": str(self.chat.session_id)}, format="json")
                assert resp.status_code == 200, resp.content
                return ("reporter", resp.json()["download"]["bundle_id"])
            adapter = DictSessionAdapter(ChatSession.objects.get(pk=self.chat.pk))
            bundle_id = _next_bundle_id(adapter)
            adapter["results_history"] = [*adapter.get("results_history", []),
                                          {"id": bundle_id, "mode": "new_search"}]
            adapter.save()
            return ("new_search", bundle_id)

        made = race(WORKERS, worker)
        ids = [bundle_id for _, bundle_id in made]
        self.assertEqual(len(set(ids)), WORKERS, made)
        self.chat.refresh_from_db()
        kept = sorted((b["mode"], b["id"]) for b in self.chat.results_history)
        self.assertEqual(kept, sorted(made))
        self.assertEqual(self.chat.extra_state[bundle_ids.BUNDLE_SEQ_KEY], max(ids))
