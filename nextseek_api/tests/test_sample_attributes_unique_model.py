"""The definitions model maps a table this repo creates in SQL, not in a migration."""

from django.conf import settings

from seek.models import Sample_attributes_unique


def test_model_maps_the_definitions_table():
    assert Sample_attributes_unique._meta.db_table == "sample_attributes_unique"


def test_model_reads_the_nextseek_database():
    """The router at seek/dbrouters.py routes on this attribute."""
    assert Sample_attributes_unique._DATABASE == settings.NEXTSEEK_DATABASE


def test_model_carries_the_three_content_fields():
    names = {f.name for f in Sample_attributes_unique._meta.get_fields()}
    assert {"field_name", "sample_type", "meaning"} <= names


def test_scope_column_defaults_to_empty_string_not_null():
    """'' is the global scope. A nullable column would let MySQL accept two
    conflicting global rows for one field, because NULLs compare distinct
    inside a unique index."""
    field = Sample_attributes_unique._meta.get_field("sample_type")
    assert field.null is False
    assert field.default == ""


def test_the_table_is_unmanaged():
    """The table is created in SQL, never by a migration. Left managed, the next
    unrelated `makemigrations` for the seek app would silently propose creating
    it — and CustomRouter.allow_migrate returns None for non-`default` app
    labels, so applying that would create it on both DB aliases."""
    assert Sample_attributes_unique._meta.managed is False
