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
    assert {"kind", "trigger_code", "add_codes", "coverage", "support",
            "assay_titles", "source", "computed_at"} <= names


def test_is_unmanaged():
    """Managed, the next unrelated makemigrations would propose creating it on
    both the default and seek aliases -- see Sample_attributes_unique."""
    assert Sample_type_requirements._meta.managed is False


def test_kind_and_trigger_are_unique_together():
    """A companion row is keyed by the PARENT it is triggered from, so the same
    code can legitimately appear once per kind -- DNA requires BAC/TIS/RNA and
    is also BAC's companion. child_code alone would have collided."""
    assert Sample_type_requirements._meta.unique_together == (("kind", "trigger_code"),)


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

    kind = get_field("kind")
    assert isinstance(kind, models.CharField)
    assert kind.max_length == 16
    assert kind.default == Sample_type_requirements.KIND_REQUIRES

    trigger_code = get_field("trigger_code")
    assert isinstance(trigger_code, models.CharField)
    assert trigger_code.max_length == 32
    # Not unique on its own: DNA is a requirement trigger AND BAC's companion
    # child, so uniqueness is (kind, trigger_code). See the test above.
    assert trigger_code.unique is False

    add_codes = get_field("add_codes")
    assert isinstance(add_codes, models.TextField)

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
