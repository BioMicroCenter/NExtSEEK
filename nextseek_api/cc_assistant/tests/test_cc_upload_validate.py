"""Hermetic filename validation for CC uploads. No Django, no Celery.

Imports from the celery-free `cc_upload_validate` module so collection never
touches the celery import block in `cc_upload_tasks.py`."""
import pytest

from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename


@pytest.mark.parametrize("good", ["data.csv", "report 1.xlsx", "a_b-c.txt"])
def test_accepts_plain_filenames(good):
    assert validate_upload_filename(good) == good


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.txt", "/abs.txt", "", "x\x00y", ".."])
def test_rejects_traversal_and_separators(bad):
    with pytest.raises(ValueError):
        validate_upload_filename(bad)
