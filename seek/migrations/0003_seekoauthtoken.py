"""Create ``seek_oauth_token`` -- one row of SEEK OAuth2 credentials per user.

Hand-written rather than produced by ``makemigrations``, so two things are
worth stating explicitly.

``AutoField``, not ``BigAutoField``: ``DEFAULT_AUTO_FIELD`` is unset in
``dmac/settings.py``, so Django falls back to ``AutoField``, which is what
``seek/0001_initial`` uses throughout. Declaring ``BigAutoField`` here would
make the model and the migration disagree and leave ``makemigrations``
permanently proposing a spurious ``AlterField``.

This is the first migration in the repository that is routed to exactly one
database. ``SeekOAuthToken._DATABASE`` is ``NEXTSEEK_DATABASE``, and the
``allow_migrate`` fix in ``seek/dbrouters.py`` means it is created on the
``default`` alias only -- not, as every prior migration in this app would have,
on the Rails-owned ``seek`` alias as well.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import seek.oauth.crypto


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("seek", "0002_samples_name_identity"),
    ]

    operations = [
        migrations.CreateModel(
            name="SeekOAuthToken",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("seek_person_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("access_token", seek.oauth.crypto.EncryptedTextField()),
                ("refresh_token", seek.oauth.crypto.EncryptedTextField(blank=True, null=True)),
                ("access_token_expires_at", models.DateTimeField()),
                ("scope", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="seek_oauth_token",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "seek_oauth_token",
            },
        ),
    ]
