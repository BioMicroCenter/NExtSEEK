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


def test_many_repeated_keys_number_in_linear_time():
    lines = "".join("!Sample_relation = x\n" for _ in range(20000))
    fields = geo.parse_soft("^SAMPLE = GSM1\n" + lines)[0].fields
    assert len(fields) == 20000
    assert fields["relation"] == "x"
    assert fields["relation.20000"] == "x"


def test_literal_key_that_collides_with_a_numbered_one_is_kept():
    text = ("^SAMPLE = GSM1\n!Sample_relation = a\n!Sample_relation = b\n"
            "!Sample_relation.2 = c\n")
    assert geo.parse_soft(text)[0].fields == {"relation": "a", "relation.2": "b", "relation.2.2": "c"}


def test_blank_and_directory_supplements_are_skipped():
    text = ("^SAMPLE = GSM1\n"
            "!Sample_supplementary_file_1 =\n"
            "!Sample_supplementary_file_2 = https://example.org/dir/\n"
            "!Sample_supplementary_file_3 = https://example.org/dir/data.txt.gz\n")
    assert [f.name for f in geo.parse_soft(text)[0].files] == ["data.txt.gz"]


def test_links_are_unique_in_first_seen_order():
    text = ("^SAMPLE = GSM1\n"
            "!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2\n"
            "!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1\n"
            "!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2\n")
    assert geo.parse_soft(text)[0].links == ["SRX2", "SRX1"]


SUPER_SOFT = """^SERIES = GSE900000
!Series_title = Super
!Series_relation = SuperSeries of: GSE900001
!Series_relation = SuperSeries of: GSE900002
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA900000
"""
SUB1_SOFT = """^SERIES = GSE900001
!Series_title = Sub one
!Series_relation = SubSeries of: GSE900000
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA900001
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP900001
"""
SUB2_SOFT = """^SERIES = GSE900002
!Series_title = Sub two (array)
!Series_relation = SubSeries of: GSE900000
"""
SUPER_GSMS_SOFT = """^SAMPLE = GSM9000011
!Sample_title = s11
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX9000011
!Sample_series_id = GSE900000
!Sample_series_id = GSE900001
^SAMPLE = GSM9000021
!Sample_title = s21
!Sample_series_id = GSE900000
!Sample_series_id = GSE900002
"""


def super_routes(sub_missing=False):
    def k(acc, targ):
        return key(geo.URL, {"acc": acc, "targ": targ, "form": "text", "view": "brief"})
    routes = {k("GSE900000", "self"): FakeResponse(200, SUPER_SOFT),
              k("GSE900000", "gsm"): FakeResponse(200, SUPER_GSMS_SOFT),
              k("GSE900001", "self"): FakeResponse(200, SUB1_SOFT),
              k("GSE900001", "gsm"): FakeResponse(200, SUPER_GSMS_SOFT.split("^SAMPLE = GSM9000021")[0]),
              k("GSE900002", "gsm"): FakeResponse(200, "^SAMPLE = GSM9000021\n!Sample_title = s21\n")}
    if not sub_missing:
        routes[k("GSE900002", "self")] = FakeResponse(200, SUB2_SOFT)
    return routes


def test_a_superseries_brings_its_subseries_projects_and_every_sample():
    f, session = fetcher(super_routes())
    records = geo.fetch("GSE900000", f)
    assert [(r.record_id, r.kind) for r in records] == [
        ("GSE900000", "project"), ("GSE900001", "project"), ("GSE900002", "project"),
        ("GSM9000011", "sample"), ("GSM9000021", "sample")]
    assert records[1].links == ["PRJNA900001", "SRP900001"]
    assert len(session.calls) == 4  # each SubSeries is read once, as itself; its samples come with the SuperSeries'


def test_a_subseries_geo_cannot_find_is_left_out_not_the_superseries():
    f, _ = fetcher(super_routes(sub_missing=True))
    assert [r.record_id for r in geo.fetch("GSE900000", f)] == ["GSE900000", "GSE900001", "GSM9000011", "GSM9000021"]


def test_a_subseries_is_never_followed_up_to_its_superseries():
    f, session = fetcher(super_routes())
    assert [r.record_id for r in geo.fetch("GSE900001", f)] == ["GSE900001", "GSM9000011"]
    assert len(session.calls) == 2
