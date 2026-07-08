"""Add a hashed samples.name_identity VIRTUAL generated column + BTREE index.

The column stores the SHA-256 hex digest of the stable non-UID identity derived
from ``json_metadata`` with the same sample-type-aware CASE precedence as the
original raw-value form:

- ``D.`` / ``A.`` prefixes prefer ``File_PrimaryData`` variants, then ``Name``
- all other prefixes prefer ``Name``, then ``File_PrimaryData`` variants

The generated-column wrapper mirrors
``nextseek_api.batch_upload.identity.hash_identity``:

``SHA2(LOWER(NULLIF(TRIM(<case-expr>), '')), 256)``

``NULLIF(TRIM(...), '')`` is load-bearing: blank-after-trim must remain NULL
instead of hashing the empty string.

MariaDB ``TRIM()`` without explicit trim characters only removes ASCII spaces,
while Python ``str.strip()`` removes broader Unicode whitespace. That mismatch
is accepted for this ASCII-oriented identity domain (sample IDs and filenames).
"""
from django.db import migrations


FORWARD_SQL = r"""
ALTER TABLE samples
  ADD COLUMN name_identity CHAR(64)
    CHARACTER SET ascii COLLATE ascii_bin
    AS (
      SHA2(
        LOWER(
          NULLIF(
            TRIM(
              CASE
                WHEN uuid LIKE 'D.%' OR uuid LIKE 'A.%' THEN
                  COALESCE(
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData_Forward')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData_Forward')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData_Reverse')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData_Reverse')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.Name'))
                  )
                ELSE
                  COALESCE(
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.Name')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData_Forward')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData_Forward')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimaryData_Reverse')),
                    JSON_UNQUOTE(JSON_EXTRACT(json_metadata, '$.File_PrimartyData_Reverse'))
                  )
              END
            ),
            ''
          )
        ),
        256
      )
    ) VIRTUAL,
  ADD INDEX idx_samples_name_identity (name_identity)
"""

REVERSE_SQL = r"""
ALTER TABLE samples
  DROP INDEX idx_samples_name_identity,
  DROP COLUMN name_identity
"""


def _forward(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    schema_editor.execute(FORWARD_SQL)


def _reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("seek", "0001_initial")]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
