"""Every pipeline Nessie can choose can also have its results brought back (spec §5).

The pinned revision is a release number or, for a pipeline with no release yet, a 40-hex
commit SHA; `PipelineMap.covers_revision` reads the first against the map's
`applies_to_versions` and the second against its `applies_to_commits`."""
import pytest

from NessieAI.ns.reingest import maps
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
    pipeline_map = maps.load(key)
    revision = ATLAS[key]["revision"]
    assert pipeline_map.covers_revision(revision), (
        key, revision, pipeline_map.applies_to_versions, pipeline_map.applies_to_commits)


def test_an_unlaunchable_atlas_pipeline_is_marked_so():
    for key, entry in ATLAS.items():
        if key not in NFCORE_PIPELINE_CATALOG:
            assert entry.get("launchable") is False, f"{key} is in no catalog but not marked launchable: false"
