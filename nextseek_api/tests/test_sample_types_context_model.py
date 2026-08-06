"""Sample_types_context maps dmac.sample_types_context, and the seed ships its rows."""

import gzip
from pathlib import Path

from seek.models import Sample_types_context

SEED = Path(__file__).resolve().parents[2] / "startup" / "seed" / "dmac.sql.gz"


def test_model_points_at_the_dmac_table():
    assert Sample_types_context._meta.db_table == "sample_types_context"
    # NEXTSEEK_DATABASE == "default" == the dmac schema; CustomRouter reads _DATABASE.
    assert Sample_types_context._DATABASE == "default"


def test_model_exposes_the_readme_columns():
    names = {f.name for f in Sample_types_context._meta.get_fields()}
    assert {"sample_type", "name", "description"} <= names


def test_tags_field_maps_to_the_capitalised_db_column():
    field = Sample_types_context._meta.get_field("tags")
    assert field.db_column == "Tags"


def test_seed_ships_the_context_table_and_rows():
    """Asserts what startup/seed/dmac.sql.gz actually contains.

    In-container this reads /app/startup/seed/dmac.sql.gz, so it fails loudly if
    the seed was not copied in alongside the code under test.
    """
    sql = gzip.decompress(SEED.read_bytes()).decode("utf-8", errors="replace")
    assert "CREATE TABLE `sample_types_context`" in sql
    insert = [ln for ln in sql.splitlines() if ln.startswith("INSERT INTO `sample_types_context`")]
    assert insert, "seed has the table but no rows"
    # 101 rows on production; the dump writes them as one extended INSERT.
    assert insert[0].count("),(") == 100
