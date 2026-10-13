"""The clones' internal-assay rows (tool spec 7 row 4, T8)."""
import pytest
from seek.models import Assays_internal_assays

from nextseek_api.studies import mapping


@pytest.mark.django_db
def test_insert_copies_each_pair_once_and_returns_every_pairs_row():
    first = mapping.insert_clone_mappings([(302, 900), (302, 905), (303, 900)])
    assert [r[1:] for r in first] == [[302, 900], [302, 905], [303, 900]]
    again = mapping.insert_clone_mappings([(302, 900), (302, 905), (303, 900), (302, 900)])
    assert again == first
    assert Assays_internal_assays.objects.filter(assay_id__in=[302, 303]).count() == 3


@pytest.mark.django_db
def test_delete_removes_only_rows_that_still_hold_their_pair():
    rows = mapping.insert_clone_mappings([(302, 900), (303, 900)])
    Assays_internal_assays.objects.filter(id=rows[1][0]).update(internal_assay_id=901)
    report = mapping.delete_clone_mappings(rows)
    assert report == {"deleted": 1, "not_deleted": [rows[1][0]]}
    assert list(Assays_internal_assays.objects.values_list("assay_id", "internal_assay_id")) == [(303, 901)]


@pytest.mark.django_db
def test_nothing_to_insert_touches_nothing():
    assert mapping.insert_clone_mappings([]) == []
