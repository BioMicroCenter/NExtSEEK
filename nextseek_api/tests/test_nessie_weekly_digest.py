"""The weekly Nessie digest: who asked what in a week, as an email body plus a per-chat CSV."""
import csv
import io
from datetime import datetime, timezone as dtz
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.management.commands import nessie_weekly_digest as D

ET = ZoneInfo("America/New_York")


def utc(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=dtz.utc)


class WindowTests(TestCase):
    def test_default_window_is_the_last_full_week_monday_to_monday_eastern(self):
        since, until = D.last_week(datetime(2026, 11, 17, 14, 0, tzinfo=ET))  # a Tuesday
        assert since == datetime(2026, 11, 9, tzinfo=ET) and until == datetime(2026, 11, 16, tzinfo=ET)

    def test_run_on_monday_morning_covers_the_week_that_just_ended(self):
        since, until = D.last_week(datetime(2026, 11, 23, 9, 0, tzinfo=ET))
        assert since == datetime(2026, 11, 16, tzinfo=ET) and until == datetime(2026, 11, 23, tzinfo=ET)


class DigestTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.ann = User.objects.create_user("ann", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.tester = User.objects.create_user("tester", password="pw")
        self.s1 = ChatSession.objects.create(user=self.ann)
        self.s2 = ChatSession.objects.create(user=self.ann)
        self.s3 = ChatSession.objects.create(user=self.bob)
        self.s4 = ChatSession.objects.create(user=self.tester)

    def _q(self, session, text, when):
        t = QueryTask.objects.create(session=session, user=session.user, query=text)
        QueryTask.objects.filter(pk=t.pk).update(created_at=when)
        return t

    def _week(self):
        return datetime(2026, 11, 10, tzinfo=ET), datetime(2026, 11, 17, tzinfo=ET)

    def test_collects_questions_in_the_window_and_drops_excluded_logins(self):
        self._q(self.s1, "How many mice?", utc(2026, 11, 10, 15))
        self._q(self.s1, "which labs?", utc(2026, 11, 10, 16))
        self._q(self.s2, "Show me the sample tree", utc(2026, 11, 12, 18))
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        self._q(self.s4, "harness question", utc(2026, 11, 11, 12))
        self._q(self.s3, "too early", utc(2026, 11, 10, 4))   # Nov 9 11 PM ET: last week
        self._q(self.s3, "too late", utc(2026, 11, 17, 6))    # Nov 17 1 AM ET: next week
        rows = D.collect(*self._week(), exclude={"tester"})
        assert [r.question for r in rows] == ["How many mice?", "which labs?", "Show me the sample tree", "TCR data?"]
        assert {r.username for r in rows} == {"ann", "bob"}

    def test_nested_container_cc_turns_are_not_questions(self):
        top = self._q(self.s3, "export the sheet", utc(2026, 11, 12, 15))
        turn = CCTurn.objects.create(task=top, user=self.bob, chat=self.s3, pass_hash="a" * 64)
        child = self._q(self.s3, "internal pass query", utc(2026, 11, 12, 15, 1))
        QueryTask.objects.filter(pk=child.pk).update(parent_cc_turn=turn)
        rows = D.collect(*self._week(), exclude=set())
        assert [r.question for r in rows] == ["export the sheet"]

    def test_body_summarises_people_chats_and_lists_every_question_with_its_date(self):
        self._q(self.s1, "How many mice?", utc(2026, 11, 10, 15))
        self._q(self.s2, "Show me the\nsample tree", utc(2026, 11, 12, 18, 30))
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        rows = D.collect(*self._week(), exclude={"tester"})
        subject, body = D.render(rows, *self._week(), instance="prod", excluded={"tester"})
        assert subject == "Nessie questions on prod, Nov 10 to Nov 16: 3 questions from 2 people"
        assert "3 questions, 2 people, 3 chats." in body
        assert "ann" in body and "2 questions in 2 chats" in body
        assert "Nov 12, 1:30 PM  Show me the sample tree" in body   # ET, one line
        assert "Not counted: tester" in body
        assert "correct" not in body.lower() and "wrong" not in body.lower()   # no grading

    def test_team_logins_go_in_their_own_section_at_the_bottom(self):
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        self._q(self.s4, "team check", utc(2026, 11, 11, 15))
        rows = D.collect(*self._week(), exclude=set())
        subject, body = D.render(rows, *self._week(), instance="prod", excluded=set(), team={"tester"})
        assert subject == "Nessie questions on prod, Nov 10 to Nov 16: 1 question from 1 person, 1 from the team"
        assert "1 question, 1 person, 1 chat." in body
        users, team = body.split("Team (")
        assert "TCR data?" in users and "team check" not in users
        assert "team check" in team and "tester" in team
        table = list(csv.DictReader(io.StringIO(D.sessions_csv(rows, team={"tester"}))))
        assert [(r["username"], r["group"]) for r in table] == [("bob", "user"), ("tester", "team")]

    def test_empty_week_still_says_so(self):
        subject, body = D.render([], *self._week(), instance="prod", excluded=set())
        assert subject.endswith(": no questions")
        assert "No one asked Nessie anything this week." in body

    def test_csv_has_one_row_per_chat_with_its_session_id(self):
        self._q(self.s1, "How many mice?", utc(2026, 11, 10, 15))
        self._q(self.s1, "which labs?", utc(2026, 11, 10, 16))
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        rows = D.collect(*self._week(), exclude=set())
        table = list(csv.DictReader(io.StringIO(D.sessions_csv(rows))))
        assert [(r["username"], r["session_id"], r["questions"]) for r in table] == [
            ("ann", str(self.s1.session_id), "2"), ("bob", str(self.s3.session_id), "1")]
        assert table[0]["first_question"] == "How many mice?"
        assert table[0]["first_asked_et"] == "2026-11-10 10:00" and table[0]["last_asked_et"] == "2026-11-10 11:00"

    def test_dry_run_prints_the_email_and_the_csv_and_sends_nothing(self):
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        out = StringIO()
        call_command("nessie_weekly_digest", "--since", "2026-11-10", "--until", "2026-11-17", stdout=out)
        text = out.getvalue()
        assert "Subject: Nessie questions" in text and "TCR data?" in text
        assert "username,group,session_id,questions" in text
        assert len(mail.outbox) == 0

    def test_send_refuses_without_smtp_settings(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(CommandError, match="NEXTSEEK_SMTP_USER"):
            call_command("nessie_weekly_digest", "--send", stdout=StringIO())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_send_mails_the_body_with_the_csv_attached(self):
        self._q(self.s3, "TCR data?", utc(2026, 11, 16, 20))
        env = {"NEXTSEEK_SMTP_USER": "u", "NEXTSEEK_SMTP_PASSWORD": "p", "NEXTSEEK_SMTP_FROM": "from@example.org",
               "NESSIE_DIGEST_TO": "dmac@example.org", "NESSIE_DIGEST_INSTANCE": "prod",
               "NESSIE_DIGEST_EXCLUDE_USERS": "tester"}
        with patch.dict("os.environ", env, clear=True), \
                patch.object(D, "_connection", side_effect=lambda: mail.get_connection()):
            call_command("nessie_weekly_digest", "--send", "--since", "2026-11-10", "--until", "2026-11-17",
                         stdout=StringIO())
        assert len(mail.outbox) == 1
        m = mail.outbox[0]
        assert m.to == ["dmac@example.org"] and m.from_email == "from@example.org"
        assert "TCR data?" in m.body
        name, content, mime = m.attachments[0]
        assert name == "nessie-chats-2026-11-10-to-2026-11-16.csv" and mime == "text/csv"
        assert str(self.s3.session_id) in content


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ScheduleTests(TestCase):
    """The beat task runs hourly; it sends once a week, after Monday 9 AM Eastern, and only where mail is set up."""
    databases = {"default"}
    ENV = {"NEXTSEEK_SMTP_USER": "u", "NEXTSEEK_SMTP_PASSWORD": "p", "NEXTSEEK_SMTP_FROM": "from@example.org",
           "NESSIE_DIGEST_TO": "dmac@example.org"}

    def setUp(self):
        import tempfile
        self.state = tempfile.mkdtemp()

    def _run(self, now, env=None):
        with patch.dict("os.environ", self.ENV if env is None else env, clear=True), \
                patch.object(D, "_connection", side_effect=lambda: mail.get_connection()):
            return D.send_if_due(now, self.state)

    def test_not_configured_is_a_quiet_no_op(self):
        assert self._run(datetime(2026, 11, 23, 10, tzinfo=ET), env={}) == "not configured"
        assert len(mail.outbox) == 0

    def test_monday_before_nine_waits(self):
        assert self._run(datetime(2026, 11, 23, 8, 59, tzinfo=ET)) == "not due"
        assert len(mail.outbox) == 0

    def test_after_nine_it_sends_last_week_once(self):
        assert self._run(datetime(2026, 11, 23, 9, 5, tzinfo=ET)).startswith("sent")
        assert self._run(datetime(2026, 11, 23, 10, 5, tzinfo=ET)) == "already sent"
        assert len(mail.outbox) == 1 and "Nov 16 to Nov 22" in mail.outbox[0].subject

    def test_a_missed_monday_is_caught_up_later_in_the_week(self):
        assert self._run(datetime(2026, 11, 25, 14, tzinfo=ET)).startswith("sent")
        assert "Nov 16 to Nov 22" in mail.outbox[0].subject

    def test_a_failed_send_is_retried_next_hour(self):
        with patch.dict("os.environ", self.ENV, clear=True), \
                patch.object(D, "_connection", side_effect=OSError("smtp down")), pytest.raises(OSError):
            D.send_if_due(datetime(2026, 11, 23, 9, 5, tzinfo=ET), self.state)
        assert self._run(datetime(2026, 11, 23, 10, 5, tzinfo=ET)).startswith("sent")


def test_the_app_scheduler_runs_the_digest_check_hourly():
    from nextseek_api.batch_upload.celery_app import app

    entry = app.conf.beat_schedule["weekly-nessie-digest"]
    assert entry["task"] == "batch_upload.weekly_nessie_digest" and entry["schedule"] == 3600.0
    assert "batch_upload.weekly_nessie_digest" in app.tasks
