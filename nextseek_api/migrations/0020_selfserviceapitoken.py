"""Create ``self_service_api_token`` -- the logout-revocation exemption marker.

Hand-written rather than produced by ``makemigrations``. Uses ``BigAutoField``
to match the rest of this app (``0002_querytask`` and its successors), which is
the opposite of ``seek/0003``: that app's ``0001_initial`` uses ``AutoField``
throughout, and a migration has to agree with the app it lives in or
``makemigrations`` proposes a spurious ``AlterField`` forever.

Depends on the merge leaf so this stays a single head.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nextseek_api", "0019_merge_attribute_async_turn_ledger"),
    ]

    operations = [
        migrations.CreateModel(
            name="SelfServiceApiToken",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("last_rotated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="self_service_api_token",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "self_service_api_token",
            },
        ),
    ]
