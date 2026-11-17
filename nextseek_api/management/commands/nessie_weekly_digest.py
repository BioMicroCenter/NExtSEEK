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
(space-separated logins left out, e.g. a test login), NESSIE_DIGEST_TEAM_USERS (space-separated logins listed
in their own section at the bottom).

Scheduled by the app's Celery beat: ``batch_upload.weekly_nessie_digest`` runs hourly and calls ``send_if_due``,
which sends once after Monday 9 AM Eastern and does nothing where the SMTP keys are unset.
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


def _plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _people_lines(by_user):
    order = sorted(by_user, key=lambda u: (-len(by_user[u]), u))
    width = max(len(u) for u in order)
    lines = []
    for u in order:
        us = by_user[u]
        first, last = _day(us[0].asked), _day(us[-1].asked)
        lines.append(f"  {u:<{width}}  {_plural(len(us), 'question')} in "
                     f"{_plural(len({r.session_id for r in us}), 'chat')}, {first if first == last else f'{first} to {last}'}")
    return order, lines


def _question_lines(by_user, order):
    lines = []
    for u in order:
        lines += ["", u]
        prev = None
        for r in by_user[u]:
            if prev is not None and r.session_id != prev:
                lines.append("    (new chat)")
            prev = r.session_id
            lines.append(f"  {_when(r.asked)}  {_one_line(r.question)}")
    return lines


def render(rows, since, until, instance="", excluded=(), team=()):
    """(subject, body). ``until`` is exclusive, so the last day shown is the day before it. Logins in ``team``
    are listed in their own section at the bottom and are not in the headline counts."""
    last = until.astimezone(ET) - timedelta(days=1)
    span = f"{_day(since.astimezone(ET))} to {_day(last)}"
    where = f" on {instance}" if instance else ""
    users, staff = defaultdict(list), defaultdict(list)
    for r in rows:
        (staff if r.username in team else users)[r.username].append(r)
    n_users, n_staff = sum(map(len, users.values())), sum(map(len, staff.values()))
    if not rows:
        subject = f"Nessie questions{where}, {span}: no questions"
    else:
        subject = (f"Nessie questions{where}, {span}: {_plural(n_users, 'question')} from "
                   f"{_plural(len(users), 'person', 'people')}")
        if n_staff:
            subject += f", {n_staff} from the team"
    lines = [f"Questions asked Nessie{where}, {span} (US Eastern)."]
    if excluded:
        lines.append(f"Not counted: {', '.join(sorted(excluded))}.")
    lines.append("")
    if not rows:
        lines.append("No one asked Nessie anything this week.")
        return subject, "\n".join(lines) + "\n"
    if users:
        chats = {r.session_id for us in users.values() for r in us}
        order, people = _people_lines(users)
        lines += [f"{_plural(n_users, 'question')}, {_plural(len(users), 'person', 'people')}, "
                  f"{_plural(len(chats), 'chat')}.", "", "People", *people, "", "Questions", *_question_lines(users, order)]
    else:
        lines.append("No one outside the team asked Nessie anything this week.")
    if staff:
        order, people = _people_lines(staff)
        lines += ["", "", f"Team ({_plural(n_staff, 'question')})", *people, *_question_lines(staff, order)]
    lines += ["", "Attached: one row per chat (username, user or team, session id, question count, first and last "
              "time, first question), for pulling the full chats."]
    return subject, "\n".join(lines) + "\n"


def sessions_csv(rows, team=()):
    chats = {}
    for r in rows:
        c = chats.setdefault(r.session_id, {"username": r.username, "group": "team" if r.username in team else "user",
                                            "session_id": r.session_id, "questions": 0,
                                            "first_asked_et": r.asked, "last_asked_et": r.asked,
                                            "first_question": " ".join(r.question.split())})
        c["questions"] += 1
        c["last_asked_et"] = r.asked
    out = io.StringIO()
    w = csv.DictWriter(out, ["username", "group", "session_id", "questions", "first_asked_et", "last_asked_et", "first_question"],
                       lineterminator="\n")
    w.writeheader()
    for c in sorted(chats.values(), key=lambda c: (c["group"] == "team", c["username"], c["first_asked_et"])):
        w.writerow({**c, "first_asked_et": f"{c['first_asked_et']:%Y-%m-%d %H:%M}",
                    "last_asked_et": f"{c['last_asked_et']:%Y-%m-%d %H:%M}"})
    return out.getvalue()


def _connection():
    return get_connection("django.core.mail.backends.smtp.EmailBackend",
                          host=os.getenv("NEXTSEEK_SMTP_HOST") or "outgoing.mit.edu",
                          port=int(os.getenv("NEXTSEEK_SMTP_PORT") or 587),
                          username=os.environ["NEXTSEEK_SMTP_USER"], password=os.environ["NEXTSEEK_SMTP_PASSWORD"],
                          use_tls=True, timeout=60)


def build(since, until):
    """(subject, body, csv name, csv text) for [since, until), with the env's exclusions and team."""
    excluded = set((os.getenv("NESSIE_DIGEST_EXCLUDE_USERS") or "").split())
    team = set((os.getenv("NESSIE_DIGEST_TEAM_USERS") or "").split())
    rows = collect(since, until, excluded)
    subject, body = render(rows, since, until, os.getenv("NESSIE_DIGEST_INSTANCE", ""), excluded, team)
    name = f"nessie-chats-{since.date()}-to-{(until - timedelta(days=1)).date()}.csv"
    return subject, body, name, sessions_csv(rows, team)


def send(since, until):
    subject, body, name, table = build(since, until)
    msg = EmailMessage(subject, body, os.environ["NEXTSEEK_SMTP_FROM"], os.environ["NESSIE_DIGEST_TO"].split(),
                       connection=_connection())
    msg.attach(name, table, "text/csv")
    msg.send()
    return subject


SEND_AT_HOUR = 9  # Monday, US Eastern


def send_if_due(now, state_dir):
    """The hourly beat task's body: mail last week's digest once, at or after Monday 9 AM Eastern. A small marker
    file under ``state_dir`` holds the last week sent, so a later hour (or a restart) never sends it twice and a
    missed Monday is caught up later that week. Where mail is not set up (dev, a laptop) it does nothing."""
    if any(not os.getenv(k) for k in SEND_ENV):
        return "not configured"
    since, until = last_week(now)
    if now.astimezone(ET) < until + timedelta(hours=SEND_AT_HOUR):
        return "not due"
    marker = os.path.join(state_dir, "nessie_weekly_digest.last_sent")
    try:
        with open(marker) as f:
            if f.read().strip() == since.date().isoformat():
                return "already sent"
    except FileNotFoundError:
        pass
    subject = send(since, until)
    os.makedirs(state_dir, exist_ok=True)
    with open(marker, "w") as f:
        f.write(since.date().isoformat())
    return f"sent {subject}"


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
        if not o["send"]:
            subject, body, name, table = build(since, until)
            self.stdout.write(f"Subject: {subject}\n\n{body}\n--- {name} ---\n{table}")
            return
        self.stdout.write(f"sent '{send(since, until)}' to {os.environ['NESSIE_DIGEST_TO']}")
