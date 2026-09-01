"""The unmanaged model over dmac.sample_type_requirements."""

from django.conf import settings

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
