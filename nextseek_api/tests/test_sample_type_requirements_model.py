"""The unmanaged model over dmac.sample_type_requirements."""

from django.conf import settings
from django.db import models

from seek.models import Sample_type_requirements


def test_maps_the_expected_table():
    assert Sample_type_requirements._meta.db_table == "sample_type_requirements"


def test_lives_on_the_nextseek_database():
    assert Sample_type_requirements._DATABASE == settings.NEXTSEEK_DATABASE


def test_carries_every_field_the_command_writes():
    names = {f.name for f in Sample_type_requirements._meta.get_fields()}
    assert {"child_code", "parent_codes", "coverage", "support",
            "assay_titles", "source", "computed_at"} <= names


def test_is_unmanaged():
    """Managed, the next unrelated makemigrations would propose creating it on
    both the default and seek aliases -- see Sample_attributes_unique."""
    assert Sample_type_requirements._meta.managed is False


def test_child_code_is_unique():
    field = Sample_type_requirements._meta.get_field("child_code")
    assert field.unique is True


def test_field_shapes_match_the_ddl():
    """Pin each field's type and constraining attributes against the DDL at
    startup/seed/sql/sample_type_requirements.sql.

    test_carries_every_field_the_command_writes only checks that field
    *names* exist -- a reviewer proved that check is too weak by mutation:
    changing `coverage` to FloatField(), `assay_titles` to null=False, and
    `source` to max_length=999 left all 5 existing tests passing. Those
    mismatches wouldn't surface until MySQL write time (a DECIMAL(4,3)
    contract silently becoming a float, or a write failing on a too-long
    value) rather than at import, which is the worst place to find them
    given later tasks write rows through this model and read `coverage`
    back with float().
    """
    get_field = Sample_type_requirements._meta.get_field

    child_code = get_field("child_code")
    assert isinstance(child_code, models.CharField)
    assert child_code.max_length == 32
    assert child_code.unique is True

    parent_codes = get_field("parent_codes")
    assert isinstance(parent_codes, models.TextField)

    coverage = get_field("coverage")
    assert isinstance(coverage, models.DecimalField)
    assert not isinstance(coverage, models.FloatField)
    assert coverage.max_digits == 4
    assert coverage.decimal_places == 3

    support = get_field("support")
    assert isinstance(support, models.IntegerField)

    assay_titles = get_field("assay_titles")
    assert isinstance(assay_titles, models.TextField)
    assert assay_titles.null is True

    source = get_field("source")
    assert isinstance(source, models.CharField)
    assert source.max_length == 16
    assert source.default == "graph"

    computed_at = get_field("computed_at")
    assert isinstance(computed_at, models.DateTimeField)
