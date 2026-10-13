"""Create dmac.project_template_bundles when it is missing.

The model is unmanaged and `./startup.sh install` creates the table from
startup/seed/sql/project_template_bundles.sql, but a rebuild never runs schema
fixups, so a box that was not freshly installed (production) has no table and
the project connections view fails with MySQL error 1146.

The DDL is a constant here, not a read of that .sql file: .dockerignore excludes
*.sql, so the app image does not contain it. A test pins the constant to the
.sql file (whitespace-normalised) so the two cannot drift. It is CREATE TABLE IF
NOT EXISTS, so a box that already has the table sees no change. Reverse is a
no-op: never drop a table install created.
"""
from django.conf import settings
from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS project_template_bundles (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  project_id INT          NOT NULL,
  position   INT          NOT NULL DEFAULT 0,
  label      VARCHAR(128) NOT NULL,
  codes      TEXT         NOT NULL,
  KEY idx_project_position (project_id, position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _forward(apps, schema_editor):
    # Only the dmac schema holds this table, and only MySQL can run the DDL.
    if schema_editor.connection.vendor != "mysql":
        return
    if schema_editor.connection.alias != settings.NEXTSEEK_DATABASE:
        return
    schema_editor.execute(DDL, params=None)


class Migration(migrations.Migration):
    # DDL on MySQL cannot roll back (same reason as 0002).
    atomic = False

    dependencies = [("seek", "0002_samples_name_identity")]

    operations = [
        migrations.RunPython(_forward, migrations.RunPython.noop),
    ]
