"""Weekly digest of who asked Nessie what: usernames, questions and dates, no grading.

Reads ``assistant_query_task`` (every chat turn has a row with its user, session and
``created_at``), so a fixed Monday-to-Monday window is the whole of "new since last time":
no state table, and re-running a past week gives the same email.

    manage.py nessie_weekly_digest                      # dry run: print last week's email and CSV
    manage.py nessie_weekly_digest --since 2026-11-10 --until 2026-11-17
    manage.py nessie_weekly_digest --send               # mail it (SMTP settings from the env)

Env (``docker/nextseek.env``): NEXTSEEK_SMTP_HOST (default outgoing.mit.edu), NEXTSEEK_SMTP_PORT
(587, STARTTLS), NEXTSEEK_SMTP_USER, NEXTSEEK_SMTP_PASSWORD, NEXTSEEK_SMTP_FROM, NESSIE_DIGEST_TO
(space-separated), NESSIE_DIGEST_INSTANCE (a label for the subject), NESSIE_DIGEST_EXCLUDE_USERS
(space-separated logins left out, e.g. the harness login).
"""
import csv
import io
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError

from nextseek_api.assistant.models_db import QueryTask

ET = ZoneInfo("America/New_York")
SEND_ENV = ("NEXTSEEK_SMTP_USER", "NEXTSEEK_SMTP_PASSWORD", "NEXTSEEK_SMTP_FROM", "NESSIE_DIGEST_TO")
MAX_Q = 300  # characters of a question shown in the email; the CSV and the database keep it whole


@dataclass
class Row:
    username: str
    session_id: str
    asked: datetime  # Eastern
    question: str


def last_week(now):
    """The last full Monday-00:00-to-Monday-00:00 week (Eastern) before ``now``."""
    now = now.astimezone(ET)
    monday = datetime(now.year, now.month, now.day, tzinfo=ET) - timedelta(days=now.weekday())
    return monday - timedelta(days=7), monday


def collect(since, until, exclude):
    """Every question asked in [since, until), oldest first. Nested Container-CC turns (a pass's own
    queries, ``parent_cc_turn`` set) are not questions a person typed, so they are left out."""
    qs = (QueryTask.objects.filter(created_at__gte=since, created_at__lt=until, parent_cc_turn__isnull=True)
          .exclude(user__username__in=exclude).select_related("user").order_by("created_at", "id"))
    return [Row(t.user.username, str(t.session_id), t.created_at.astimezone(ET), t.query or "") for t in qs]


def _one_line(text, limit=MAX_Q):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _day(d):
    return f"{d:%b} {d.day}"


def _when(d):
    return f"{_day(d)}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def render(rows, since, until, instance="", excluded=()):
    """(subject, body). ``until`` is exclusive, so the last day shown is the day before it."""
    last = until.astimezone(ET) - timedelta(days=1)
    span = f"{_day(since.astimezone(ET))} to {_day(last)}"
    where = f" on {instance}" if instance else ""
    by_user = defaultdict(list)
    for r in rows:
        by_user[r.username].append(r)
    if not rows:
        subject = f"Nessie questions{where}, {span}: no questions"
    else:
        subject = (f"Nessie questions{where}, {span}: {len(rows)} question{'s' * (len(rows) != 1)} "
                   f"from {len(by_user)} {'person' if len(by_user) == 1 else 'people'}")
    lines = [f"Questions asked Nessie{where}, {span} (US Eastern)."]
    if excluded:
        lines.append(f"Not counted: {', '.join(sorted(excluded))}.")
    lines.append("")
    if not rows:
        lines.append("No one asked Nessie anything this week.")
        return subject, "\n".join(lines) + "\n"
    chats = {r.session_id for r in rows}
    lines += [f"{len(rows)} questions, {len(by_user)} people, {len(chats)} chats.", "", "People"]
    order = sorted(by_user, key=lambda u: (-len(by_user[u]), u))
    width = max(len(u) for u in order)
    for u in order:
        us = by_user[u]
        n_chats = len({r.session_id for r in us})
        first, lastd = _day(us[0].asked), _day(us[-1].asked)
        dates = first if first == lastd else f"{first} to {lastd}"
        lines.append(f"  {u:<{width}}  {len(us)} question{'s' * (len(us) != 1)} in {n_chats} "
                     f"chat{'s' * (n_chats != 1)}, {dates}")
    lines += ["", "Questions"]
    for u in order:
        lines += ["", u]
        prev = None
        for r in by_user[u]:
            if prev is not None and r.session_id != prev:
                lines.append("    (new chat)")
            prev = r.session_id
            lines.append(f"  {_when(r.asked)}  {_one_line(r.question)}")
    lines += ["", "Attached: one row per chat (username, session id, question count, first and last time, "
              "first question), for pulling the full chats."]
    return subject, "\n".join(lines) + "\n"


def sessions_csv(rows):
    chats = {}
    for r in rows:
        c = chats.setdefault(r.session_id, {"username": r.username, "session_id": r.session_id, "questions": 0,
                                            "first_asked_et": r.asked, "last_asked_et": r.asked,
                                            "first_question": " ".join(r.question.split())})
        c["questions"] += 1
        c["last_asked_et"] = r.asked
    out = io.StringIO()
    w = csv.DictWriter(out, ["username", "session_id", "questions", "first_asked_et", "last_asked_et", "first_question"],
                       lineterminator="\n")
    w.writeheader()
    for c in sorted(chats.values(), key=lambda c: (c["username"], c["first_asked_et"])):
        w.writerow({**c, "first_asked_et": f"{c['first_asked_et']:%Y-%m-%d %H:%M}",
                    "last_asked_et": f"{c['last_asked_et']:%Y-%m-%d %H:%M}"})
    return out.getvalue()


def _connection():
    return get_connection("django.core.mail.backends.smtp.EmailBackend",
                          host=os.getenv("NEXTSEEK_SMTP_HOST") or "outgoing.mit.edu",
                          port=int(os.getenv("NEXTSEEK_SMTP_PORT") or 587),
                          username=os.environ["NEXTSEEK_SMTP_USER"], password=os.environ["NEXTSEEK_SMTP_PASSWORD"],
                          use_tls=True, timeout=60)


def _date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=ET)
    except ValueError as e:
        raise CommandError(f"dates are YYYY-MM-DD (Eastern midnight): {s}") from e


class Command(BaseCommand):
    help = "Email (or with no --send, print) last week's Nessie questions: who asked what, and a per-chat CSV."

    def add_arguments(self, parser):
        parser.add_argument("--since", help="YYYY-MM-DD, Eastern midnight, inclusive (default: last Monday week)")
        parser.add_argument("--until", help="YYYY-MM-DD, Eastern midnight, exclusive (default: this Monday)")
        parser.add_argument("--send", action="store_true", help="send the email; without it, print it")

    def handle(self, *args, **o):
        if o["send"]:
            missing = [k for k in SEND_ENV if not os.getenv(k)]
            if missing:
                raise CommandError(f"--send needs {', '.join(missing)} in the environment (docker/nextseek.env)")
        since, until = last_week(datetime.now(ET))
        since = _date(o["since"]) if o["since"] else since
        until = _date(o["until"]) if o["until"] else until
        if since >= until:
            raise CommandError("--since must be before --until")
        excluded = set((os.getenv("NESSIE_DIGEST_EXCLUDE_USERS") or "").split())
        rows = collect(since, until, excluded)
        subject, body = render(rows, since, until, os.getenv("NESSIE_DIGEST_INSTANCE", ""), excluded)
        table = sessions_csv(rows)
        last = (until - timedelta(days=1)).date()
        name = f"nessie-chats-{since.date()}-to-{last}.csv"
        if not o["send"]:
            self.stdout.write(f"Subject: {subject}\n\n{body}\n--- {name} ---\n{table}")
            return
        msg = EmailMessage(subject, body, os.environ["NEXTSEEK_SMTP_FROM"], os.environ["NESSIE_DIGEST_TO"].split(),
                           connection=_connection())
        msg.attach(name, table, "text/csv")
        msg.send()
        self.stdout.write(f"sent '{subject}' to {os.environ['NESSIE_DIGEST_TO']}")
