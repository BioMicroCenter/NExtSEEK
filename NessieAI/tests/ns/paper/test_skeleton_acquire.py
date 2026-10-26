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
