from types import SimpleNamespace

from django.test import override_settings

from seek.dbrouters import CustomRouter


def _obj(db):
    return SimpleNamespace(_state=SimpleNamespace(db=db))


@override_settings(SEEK_ALIAS_MIGRATABLE=False)
def test_seek_alias_not_migrated_by_default():
    assert CustomRouter().allow_migrate("seek", "seek") is False


@override_settings(SEEK_ALIAS_MIGRATABLE=True)
def test_seek_alias_migratable_when_flagged():
    assert CustomRouter().allow_migrate("seek", "seek") is None


def test_allow_relation_same_db():
    r = CustomRouter()
    assert r.allow_relation(_obj("seek"), _obj("seek")) is True
    assert r.allow_relation(_obj("default"), _obj("default")) is True
    assert r.allow_relation(_obj("seek"), _obj("default")) is False
