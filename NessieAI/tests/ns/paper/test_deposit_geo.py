from datetime import date

from NessieAI.ns.paper.deposits import geo
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, key
from NessieAI.tests.ns.paper.fixtures import GEO_SAMPLES_SOFT, GEO_SERIES_SOFT


def _routes():
    common = {"acc": "GSE123456", "form": "text", "view": "brief"}
    return {key(geo.URL, {**common, "targ": "self"}): FakeResponse(200, GEO_SERIES_SOFT),
            key(geo.URL, {**common, "targ": "gsm"}): FakeResponse(200, GEO_SAMPLES_SOFT)}


def test_fetch_series_and_samples():
    f, _ = fetcher(_routes())
    records = geo.fetch("GSE123456", f)
    assert [(r.record_id, r.kind) for r in records] == [
        ("GSE123456", "project"), ("GSM7000001", "sample"), ("GSM7000002", "sample")]
    series, first = records[0], records[1]
    assert series.links == ["PRJNA100001", "SRP400001"]
    assert series.submitted == date(2024, 1, 5)
    assert first.title == "tumour_rep1"
    assert first.fields["characteristics.strain"] == "C57BL/6"
    assert first.fields["characteristics.age"] == "8 weeks"
    assert first.fields["source_name_ch1"] == "tumour"
    assert first.links == ["SRX2000001"]
    assert [f.name for f in first.files] == ["GSM7000001_counts.txt.gz"]


def test_repeated_plain_keys_get_suffixes():
    records = geo.parse_soft("^SAMPLE = GSM1\n!Sample_relation = a\n!Sample_relation = b\n")
    assert records[0].fields == {"relation": "a", "relation.2": "b"}


def test_single_sample_skips_series_call():
    f, session = fetcher({key(geo.URL, {"acc": "GSM7000001", "targ": "gsm", "form": "text", "view": "brief"}):
                          FakeResponse(200, GEO_SAMPLES_SOFT.split("^SAMPLE = GSM7000002")[0])})
    assert [r.record_id for r in geo.fetch("GSM7000001", f)] == ["GSM7000001"]
    assert len(session.calls) == 1
