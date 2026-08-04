import json

from nessie_tests import bayes_manifest as bm
from nessie_tests.manifest import NessieManifestEntry


def _entry(vid="x.y", status="passed", cost=None):
    return NessieManifestEntry(id=vid, family="f", tier="full", status=status, cost=cost)


def test_a_pair_holds_both_arms():
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype="Search-Basic",
                     ns=_entry(), cc=_entry())
    assert p.ns.status == "passed" and p.cc.status == "passed"


def test_a_half_finished_pair_is_representable():
    """Pairs are written as they complete so --resume works. A pair whose CC arm
    has not run yet must round-trip rather than fail validation."""
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype=None, ns=_entry(), cc=None)
    assert bm.BayesPair.model_validate(json.loads(p.model_dump_json())).cc is None


def test_manifest_round_trips_through_disk(tmp_path):
    m = bm.BayesManifest(run_meta={"mode": "bayesian"},
                         pairs=[bm.BayesPair(id="x.y", family="f", hibayes_subtype=None,
                                             ns=_entry(), cc=_entry())])
    bm.write_bayes_manifest(m, tmp_path)
    assert bm.read_bayes_manifest(tmp_path).pairs[0].id == "x.y"


def test_read_returns_none_when_there_is_nothing_to_resume(tmp_path):
    assert bm.read_bayes_manifest(tmp_path) is None


def test_completed_arms_reports_only_arms_that_actually_ran():
    m = bm.BayesManifest(run_meta={}, pairs=[
        bm.BayesPair(id="a", family="f", hibayes_subtype=None, ns=_entry("a"), cc=_entry("a")),
        bm.BayesPair(id="b", family="f", hibayes_subtype=None, ns=_entry("b"), cc=None),
    ])
    assert bm.completed_arms(m) == {("a", "ns"), ("a", "cc"), ("b", "ns")}
