"""The Container-CC transcript recover endpoint (#98).

GET /nextseek_api/cc-assistant/transcript/<session>/<turn>/ serves one
CCSessionTranscript row's blob, decompressed, as ndjson. Its alias is
/nextseek_api/nessie/sessions/<session>/transcript/<turn>/.

#68 changed what a row holds and when it is written, and nothing pinned the
endpoint afterwards:

* a row written after #68 holds only its own turn's records, so the endpoint
  must answer with that turn's slice and nothing from a neighbouring turn;
* a failed turn now gets a row (store_transcript on the error path), so it
  answers 200, not 404;
* a turn with no row still answers 404, as does a chat the caller does not own;
* a row written before #68 holds the cumulative session (turns 1..N). The
  endpoint does no slicing: it serves the stored bytes as they are, and a
  reader folds rows by containment (CCSessionTranscript's docstring).
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from NessieAI.cc.cc_transcript_store import compress, store_transcript
from nextseek_api.assistant.models_db import ChatSession, CCSessionTranscript

T1 = b'{"type":"user","turn":"t1","content":"how many mice"}\n{"type":"assistant","turn":"t1"}\n'
T2 = b'{"type":"user","turn":"t2","content":"and rats"}\n{"type":"assistant","turn":"t2"}\n'
FAILED = (b'{"type":"user","turn":"t3","content":"plot it"}\n'
          b'{"type":"result","subtype":"error_during_execution","is_error":true}\n')


def _url(session, turn, prefix="cc-assistant"):
    if prefix == "nessie":
        return f"/nextseek_api/nessie/sessions/{session}/transcript/{turn}/"
    return f"/nextseek_api/cc-assistant/transcript/{session}/{turn}/"


def _row(cs, cc_session_id, turn_id, raw):
    """A row as the success path writes it (NessieAI/cc/turn.py), straight to the ORM."""
    CCSessionTranscript.objects.create(chat_session=cs, cc_session_id=cc_session_id, turn_id=turn_id,
                                       blob=compress(raw), uncompressed_size=len(raw))


def _body(resp):
    return b"".join(resp.streaming_content) if resp.streaming else resp.content


class RecoverTranscriptTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("transcript-owner", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.cs = ChatSession.objects.create(user=self.user)
        _row(self.cs, "cc-a", "t1", T1)
        _row(self.cs, "cc-a", "t2", T2)

    def _get(self, turn, **kw):
        return self.client.get(_url(self.cs.session_id, turn, **kw))

    def test_a_turn_answers_with_its_own_slice_only(self):
        for turn, want in (("t1", T1), ("t2", T2)):
            with self.subTest(turn=turn):
                resp = self._get(turn)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(_body(resp), want)
                self.assertEqual(resp["Content-Type"], "application/x-ndjson")
                self.assertIn(f'filename="transcript-{turn}.jsonl"', resp["Content-Disposition"])

    def test_the_nessie_alias_serves_the_same_bytes(self):
        self.assertEqual(_body(self._get("t2", prefix="nessie")), T2)

    def test_a_failed_turn_answers_200_with_its_row(self):
        # store_transcript is the writer #68 added for the error paths.
        store_transcript(chat_session=self.cs, cc_session_id="cc-a", turn_id="t3", raw_jsonl=FAILED)
        resp = self._get("t3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_body(resp), FAILED)

    def test_a_turn_with_no_row_answers_404(self):
        self.assertEqual(self._get("t9").status_code, 404)

    def test_a_session_with_no_rows_answers_404(self):
        empty = ChatSession.objects.create(user=self.user)
        self.assertEqual(self.client.get(_url(empty.session_id, "t1")).status_code, 404)

    def test_another_users_chat_answers_404(self):
        other = APIClient()
        other.force_authenticate(user=User.objects.create_user("someone-else", password="pw"))
        resp = other.get(_url(self.cs.session_id, "t1"))
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b"how many mice", _body(resp))

    def test_a_superuser_reads_another_users_chat(self):
        admin = APIClient()
        admin.force_authenticate(user=User.objects.create_superuser("transcript-admin", password="pw"))
        resp = admin.get(_url(self.cs.session_id, "t1"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_body(resp), T1)

    def test_anonymous_is_refused(self):
        self.assertIn(APIClient().get(_url(self.cs.session_id, "t1")).status_code, (401, 403))

    def test_two_cc_sessions_on_one_turn_need_cc_session_id(self):
        # A wiped cc-state store starts a fresh cc session, so one turn_id can carry two rows.
        _row(self.cs, "cc-b", "t1", T2)
        self.assertEqual(self._get("t1").status_code, 400)
        url = _url(self.cs.session_id, "t1")
        self.assertEqual(_body(self.client.get(url, {"cc_session_id": "cc-a"})), T1)
        self.assertEqual(_body(self.client.get(url, {"cc_session_id": "cc-b"})), T2)

    def test_a_pre_68_cumulative_row_is_served_verbatim(self):
        # Before #68, row N held the whole --resume session file: turns 1..N.
        legacy = ChatSession.objects.create(user=self.user)
        _row(legacy, "cc-old", "t1", T1)
        _row(legacy, "cc-old", "t2", T1 + T2)
        first = _body(self.client.get(_url(legacy.session_id, "t1")))
        second = _body(self.client.get(_url(legacy.session_id, "t2")))
        self.assertEqual(first, T1)
        # No slicing on read: the cumulative row comes back whole, and it still
        # starts with turn 1, which is what lets a reader fold rows by containment.
        self.assertEqual(second, T1 + T2)
        self.assertTrue(second.startswith(first))
