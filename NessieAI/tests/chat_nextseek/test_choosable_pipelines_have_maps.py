"""Every pipeline Nessie can choose can also have its results brought back (spec §5)."""
import json

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from NessieAI.paths import NESSIE_ROOT
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.nfcore_atlas import load_atlas

MAPS = NESSIE_ROOT / "ns" / "reingest_maps"
ATLAS = load_atlas()["pipelines"]
LAUNCHABLE = sorted(k for k, e in ATLAS.items() if e.get("launchable", True))


@pytest.mark.parametrize("key", LAUNCHABLE)
def test_a_choosable_pipeline_is_in_the_catalog(key):
    assert key in NFCORE_PIPELINE_CATALOG


@pytest.mark.parametrize("key", LAUNCHABLE)
def test_a_choosable_pipeline_has_a_map_for_its_pinned_release(key):
    path = MAPS / f"{key}.outputs.json"
    assert path.exists(), f"{key} is choosable but has no reingest map"
    spec = SpecifierSet(json.loads(path.read_text())["applies_to_versions"])
    assert Version(ATLAS[key]["revision"]) in spec, (key, ATLAS[key]["revision"], str(spec))


def test_an_unlaunchable_atlas_pipeline_is_marked_so():
    for key, entry in ATLAS.items():
        if key not in NFCORE_PIPELINE_CATALOG:
            assert entry.get("launchable") is False, f"{key} is in no catalog but not marked launchable: false"
