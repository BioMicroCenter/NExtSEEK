import io
import zipfile

import pytest

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.acquire import acquire
from NessieAI.ns.paper.deposits import ena, geo, pride
from NessieAI.ns.paper.fetch import SourceNotFound, SourceUnavailable
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import DepositRecord, PaperArtifact, PaperRef
from NessieAI.ns.paper.skeleton import build_skeleton
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher
from NessieAI.tests.ns.paper.fixtures import ENA_RUNS, GEO_SAMPLES_SOFT, GEO_SERIES_SOFT, JATS_XML, PRIDE_FILES


def _geo_records():
    return geo.parse_soft(GEO_SERIES_SOFT) + geo.parse_soft(GEO_SAMPLES_SOFT)


def _ena_records():
    return [ena._record(r) for r in ENA_RUNS]


def _pride_records():
    project = DepositRecord(repo="pride", record_id="PXD012345", kind="project")
    return [project] + [pride._file("PXD012345", f, None) for f in PRIDE_FILES]


def test_skeleton_folds_runs_into_gsms_and_groups_the_rest():
    rows = build_skeleton(_geo_records() + _ena_records() + _pride_records())
    assert [(r.row_key, r.kind) for r in rows] == [
        ("geo:GSM7000001", "raw"), ("geo:GSM7000002", "raw"), ("ena:SRX2000009", "raw"),
        ("pride:PXD012345/A1.raw", "raw"), ("pride:PXD012345/A2.raw", "raw"),
        ("pride:PXD012345/search.mzid.gz", "analysis"),
    ]
    gsm1 = rows[0]
    assert [f.name for f in gsm1.files] == ["GSM7000001_counts.txt.gz", "SRR3000001_1.fastq.gz", "SRR3000001_2.fastq.gz"]
    assert "E:ena:SRR3000001:library_strategy" in gsm1.evidence_ids


def _paper(text_source="pdf"):
    text = parse_jats(JATS_XML.replace(b"GSE123456.", b"GSE123456 and PRIDE PXD012345 (GSM7000001)."))
    return PaperArtifact(ref=PaperRef(pmid="1", pmcid="PMC1"), text=text, text_source=text_source, mode="new")


def _adapters(calls, fail=None):
    def make(repo, records):
        def adapter(acc, f):
            calls.append((repo, acc))
            if fail and fail[0] == (repo, acc):
                raise fail[1]
            return records
        return adapter
    return {"geo": make("geo", _geo_records()), "ena": make("ena", _ena_records()),
            "pride": make("pride", _pride_records()), "zenodo": make("zenodo", []),
            "figshare": make("figshare", []), "sdr": make("sdr", [])}


def test_acquire_follows_geo_to_ena_and_skips_covered_accessions():
    calls = []
    f, _ = fetcher({})
    evidence = acquire(_paper(), f, adapters=_adapters(calls))
    assert calls == [("geo", "GSE123456"), ("pride", "PXD012345"), ("ena", "PRJNA100001")]
    assert len(evidence.skeleton) == 6
    ids = {i.id for i in evidence.items}
    assert "E:methods:p1" in ids and "E:geo:GSM7000001:characteristics.strain" in ids
    assert evidence.gaps[0].kind == "supplement_unavailable"
    assert all(set(row.evidence_ids) <= ids for row in evidence.skeleton)


def test_acquire_reads_the_europepmc_supplement_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("NIHMS1_TableS1.xlsx.csv", "a\n")
    f, _ = fetcher({f"{europepmc.BASE}/PMC1/supplementaryFiles": FakeResponse(200, buf.getvalue())})
    evidence = acquire(_paper("europepmc"), f, adapters=_adapters([]))
    assert any(i.id.startswith("E:si:NIHMS1_TableS1.xlsx.csv!") for i in evidence.items)
    assert [g.detail for g in evidence.gaps if g.kind == "supplement_unavailable"] == ["NIHMS1_TableS1.xlsx"]


def test_not_found_is_a_gap_and_unavailable_propagates():
    f, _ = fetcher({})
    evidence = acquire(_paper(), f, adapters=_adapters([], fail=(("pride", "PXD012345"), SourceNotFound("u", 404))))
    assert ("deposit_unavailable", "pride PXD012345: HTTP 404") in {(g.kind, g.detail) for g in evidence.gaps}
    with pytest.raises(SourceUnavailable):
        acquire(_paper(), f, adapters=_adapters([], fail=(("geo", "GSE123456"), SourceUnavailable("h", "503"))))


def test_a_cited_run_never_hides_the_series_sra_project():
    text = parse_jats(JATS_XML.replace(b"GSE123456.", b"GSE123456 (run SRR3000001)."))
    paper = PaperArtifact(ref=PaperRef(pmid="1", pmcid="PMC1"), text=text, text_source="pdf", mode="new")
    calls = []

    def ena_adapter(acc, f):
        calls.append(acc)
        assert acc != "SRP400001"
        return [ena._record(ENA_RUNS[0])] if acc == "SRR3000001" else _ena_records()

    adapters = _adapters([])
    adapters["ena"] = ena_adapter
    f, _ = fetcher({})
    evidence = acquire(paper, f, adapters=adapters)
    assert "PRJNA100001" in calls
    rows = {r.row_key: r for r in evidence.skeleton if r.repo in ("geo", "ena")}
    assert sorted(rows) == ["ena:SRX2000009", "geo:GSM7000001", "geo:GSM7000002"]
    assert rows["geo:GSM7000002"].files and any(f.name.endswith(".fastq.gz") for f in rows["geo:GSM7000002"].files)


def test_a_deposit_with_only_a_project_record_is_an_empty_gap():
    text = parse_jats(JATS_XML.replace(b"GSE123456.", b"GSE123456 and 10.5281/zenodo.123456."))
    paper = PaperArtifact(ref=PaperRef(pmid="1", pmcid="PMC1"), text=text, text_source="pdf", mode="new")
    adapters = _adapters([])
    adapters["geo"] = lambda acc, f: []
    adapters["zenodo"] = lambda acc, f: [DepositRecord(repo="zenodo", record_id="123456", kind="project", title="t")]
    f, _ = fetcher({})
    evidence = acquire(paper, f, adapters=adapters)
    assert ("deposit_empty", "zenodo 123456") in {(g.kind, g.detail) for g in evidence.gaps}
    assert [r for r in evidence.skeleton if r.repo == "zenodo"] == []
    assert [d.record_id for d in evidence.deposits if d.repo == "zenodo"] == ["123456"]


def test_a_deposit_that_answers_badly_is_a_gap_not_a_failed_run():
    import json

    bad = json.JSONDecodeError("Expecting value", "<html>", 0)
    f, _ = fetcher({})
    evidence = acquire(_paper(), f, adapters=_adapters([], fail=(("ena", "PRJNA100001"), bad)))
    assert [r.row_key for r in evidence.skeleton if r.repo == "geo"] == ["geo:GSM7000001", "geo:GSM7000002"]
    assert ("deposit_unavailable", "ena PRJNA100001: JSONDecodeError") in {(g.kind, g.detail) for g in evidence.gaps}


def test_a_deposit_that_redirects_off_the_allowlist_is_a_gap():
    from NessieAI.ns.paper.fetch import HostNotAllowed

    f, _ = fetcher({})
    evidence = acquire(_paper(), f, adapters=_adapters([], fail=(("pride", "PXD012345"), HostNotAllowed("http://x"))))
    assert ("deposit_unavailable", "pride PXD012345: HostNotAllowed") in {(g.kind, g.detail) for g in evidence.gaps}
    assert [r for r in evidence.skeleton if r.repo == "pride"] == []


def test_running_out_of_time_propagates():
    from NessieAI.ns.paper.fetch import OutOfTime

    f, _ = fetcher({})
    with pytest.raises(OutOfTime):
        acquire(_paper(), f, adapters=_adapters([], fail=(("pride", "PXD012345"), OutOfTime("t"))))


def _paper_citing(text: bytes, text_source="pdf", **ref) -> PaperArtifact:
    jats = parse_jats(JATS_XML.replace(b"GSE123456.", text))
    return PaperArtifact(ref=PaperRef(pmid="1", **ref), text=jats, text_source=text_source, mode="new")


def test_a_paper_citing_only_gsms_folds_their_sra_runs_in():
    calls = []
    gsm1 = geo.parse_soft(GEO_SAMPLES_SOFT.split("^SAMPLE = GSM7000002")[0])

    def geo_adapter(acc, f):
        calls.append(("geo", acc))
        return gsm1

    def ena_adapter(acc, f):
        calls.append(("ena", acc))
        return [ena._record(ENA_RUNS[0])] if acc == "SRX2000001" else []

    adapters = _adapters([])
    adapters.update(geo=geo_adapter, ena=ena_adapter)
    f, _ = fetcher({})
    evidence = acquire(_paper_citing(b"GSM7000001."), f, adapters=adapters)
    assert calls == [("geo", "GSM7000001"), ("ena", "SRX2000001")]
    [row] = evidence.skeleton
    assert row.row_key == "geo:GSM7000001"
    assert [x.name for x in row.files] == ["GSM7000001_counts.txt.gz", "SRR3000001_1.fastq.gz", "SRR3000001_2.fastq.gz"]


def test_gsms_of_a_cited_series_never_fetch_their_runs_one_by_one():
    calls = []
    f, _ = fetcher({})
    acquire(_paper_citing(b"GSE123456 (GSM7000001, GSM7000002)."), f, adapters=_adapters(calls))
    assert [c for c in calls if c[0] == "ena"] == [("ena", "PRJNA100001")]


def _zip_with(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "a\n")
    return buf.getvalue()


def test_an_open_access_pdf_paper_with_a_pmcid_reads_the_europepmc_supplement():
    f, session = fetcher({f"{europepmc.BASE}/PMC1/supplementaryFiles": FakeResponse(200, _zip_with("TableS2.csv"))})
    evidence = acquire(_paper_citing(b"GSE123456.", pmcid="PMC1", open_access=True), f, adapters=_adapters([]))
    assert any(i.id.startswith("E:si:TableS2.csv!") for i in evidence.items)


def test_a_closed_pdf_paper_never_asks_for_the_supplement():
    f, session = fetcher({f"{europepmc.BASE}/PMC1/supplementaryFiles": FakeResponse(200, _zip_with("TableS2.csv"))})
    evidence = acquire(_paper_citing(b"GSE123456.", pmcid="PMC1"), f, adapters=_adapters([]))
    assert session.calls == []
    assert not any(i.id.startswith("E:si:") for i in evidence.items)


def _super_adapters(calls, umbrella_runs=True):
    from NessieAI.tests.ns.paper.test_deposit_geo import super_routes

    geo_fetcher, _ = fetcher(super_routes())
    run11 = {"run_accession": "SRR9000011", "experiment_accession": "SRX9000011", "study_accession": "PRJNA900001",
             "secondary_study_accession": "SRP900001", "experiment_title": "s11 RNA-seq",
             "fastq_ftp": "ftp.sra.ebi.ac.uk/x/SRR9000011.fastq.gz", "fastq_bytes": "5", "fastq_md5": "ab"}

    def geo_adapter(acc, f):
        calls.append(("geo", acc))
        return geo.fetch(acc, geo_fetcher)

    def ena_adapter(acc, f):
        calls.append(("ena", acc))
        if acc == "PRJNA900000":
            return [ena._record(run11)] if umbrella_runs else []
        return [ena._record(run11)] if acc in ("PRJNA900001", "SRP900001") else []

    adapters = _adapters([])
    adapters.update(geo=geo_adapter, ena=ena_adapter)
    return adapters


@pytest.mark.parametrize("umbrella_runs", [True, False])
def test_a_superseries_gives_one_row_per_gsm_with_runs_even_when_the_subseries_is_cited_too(umbrella_runs):
    calls = []
    f, _ = fetcher({})
    paper = _paper_citing(b"GSE900000 (subseries GSE900001).")
    evidence = acquire(paper, f, adapters=_super_adapters(calls, umbrella_runs))
    rows = {r.row_key: r for r in evidence.skeleton}
    assert sorted(rows) == ["geo:GSM9000011", "geo:GSM9000021"]
    assert [x.name for x in rows["geo:GSM9000011"].files] == ["SRR9000011.fastq.gz"]
    assert [c for c in calls if c[0] == "geo"] == [("geo", "GSE900000")]
    assert sorted(d.record_id for d in evidence.deposits if d.kind == "project") == ["GSE900000", "GSE900001", "GSE900002"]
    ena_calls = [c[1] for c in calls if c[0] == "ena"]
    assert ena_calls == (["PRJNA900000"] if umbrella_runs else ["PRJNA900000", "PRJNA900001"])
