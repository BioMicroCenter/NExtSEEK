import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import connections, migrations, models, transaction

ADMIN_ROLE_TYPE_ID = 1  # SEEK's admin role type


def link_accounts(User, Link, seek_rows, admin_person_ids):
    """Link each auth user whose username is a SEEK login. Returns (linked, unlinked superuser names).

    ``seek_rows``: (login, person_id, email). Logins match case-insensitively (SEEK logins are unique that way).
    Non-superusers are linked by login. A superuser is linked only when its email equals the SEEK person's
    (case-insensitive) or the person is a SEEK admin. One bad row is printed and skipped, never the rest.
    """
    linked, unlinked, used = 0, [], set()
    by_login = {str(login).casefold(): (int(pid), email) for login, pid, email in seek_rows if pid is not None}
    for user in User.objects.all().order_by("pk"):
        try:
            hit = by_login.get(user.username.casefold())
            if hit is None:
                if user.is_superuser:
                    unlinked.append(user.username)
                continue
            pid, email = hit
            if pid in used or Link.objects.filter(person_id=pid).exists() or Link.objects.filter(user_id=user.pk).exists():
                continue
            if user.is_superuser:
                same_email = bool(user.email) and bool(email) and user.email.lower() == str(email).lower()
                if not (same_email or pid in admin_person_ids):
                    unlinked.append(user.username)
                    continue
            with transaction.atomic():
                Link.objects.create(user_id=user.pk, person_id=pid)
            used.add(pid)
            linked += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest
            print(f"  SeekPersonLink backfill: skipped {user.username}: {type(exc).__name__}")
            if user.is_superuser:
                unlinked.append(user.username)
    return linked, sorted(unlinked)


def backfill(apps, schema_editor):
    """Never raises: a failure here would stop the app at container start, so it prints and skips."""
    try:
        if schema_editor.connection.alias != "default":
            return
        from django.conf import settings as dj_settings

        with connections[dj_settings.SEEK_DATABASE].cursor() as cursor:
            cursor.execute("SELECT u.login, u.person_id, p.email FROM users u JOIN people p ON p.id = u.person_id")
            rows = cursor.fetchall()
            cursor.execute("SELECT DISTINCT person_id FROM roles WHERE role_type_id = %s", [ADMIN_ROLE_TYPE_ID])
            admins = {int(r[0]) for r in cursor.fetchall() if r[0] is not None}
        User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
        linked, unlinked = link_accounts(User, apps.get_model("nextseek_api", "SeekPersonLink"), rows, admins)
        print(f"\n  SeekPersonLink backfill: linked {linked} account(s).")
        if unlinked:
            print("  Superusers left unlinked (link them in /admin/ if they sign in with SEEK): " + ", ".join(unlinked))
    except Exception as exc:  # noqa: BLE001 - see docstring
        print(f"\n  SeekPersonLink backfill skipped: {type(exc).__name__}: {exc}")


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nextseek_api", "0027_ccturn_ops_cost_estimated"),
    ]

    operations = [
        migrations.CreateModel(
            name="SeekPersonLink",
            fields=[
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                                              serialize=False, to=settings.AUTH_USER_MODEL)),
                ("person_id", models.IntegerField(unique=True)),
                ("linked_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
