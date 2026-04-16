"""Add samples.name_identity VIRTUAL generated column + BTREE index."""
from django.db import migrations


FORWARD_SQL = r"""
ALTER TABLE samples
  ADD COLUMN name_identity VARCHAR(255)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    AS (
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
    ) VIRTUAL,
  ADD INDEX idx_samples_name_identity (name_identity)
"""

REVERSE_SQL = r"""
ALTER TABLE samples
  DROP INDEX idx_samples_name_identity,
  DROP COLUMN name_identity
"""


class Migration(migrations.Migration):
    dependencies = [("seek", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql=FORWARD_SQL,
            reverse_sql=REVERSE_SQL,
        ),
    ]
