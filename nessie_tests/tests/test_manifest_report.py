from nessie_tests import manifest as M
from nessie_tests import report


def _sample():
    return M.NessieManifest(
        started_at="t0", ended_at="t1", tier="route", scope="specific",
        entries=[M.NessieManifestEntry(id="route.cc_pipeline_launch", family="nessie_route",
                                       tier="route", status="passed", route="container_cc",
                                       engine="container_cc:opus")])


def test_manifest_roundtrip(tmp_path):
    p = tmp_path / "manifest.json"
    M.write_manifest(_sample(), p)
    loaded = M.load_manifest(p)
    assert loaded.entries[0].route == "container_cc"
    assert loaded.tier == "route"


def test_generate_html_contains_id(tmp_path):
    out = report.generate_html(_sample(), tmp_path)
    assert out.name == "report.html"
    assert "route.cc_pipeline_launch" in out.read_text()
