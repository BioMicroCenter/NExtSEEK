import json
from datetime import date

from NessieAI.ns.paper.deposits import ena
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, key
from NessieAI.tests.ns.paper.fixtures import ENA_RUNS


def _route(acc, body):
    return {key(ena.URL, {"accession": acc, "result": "read_run", "fields": ",".join(ena.FIELDS),
                          "format": "json"}): FakeResponse(200, body)}


def test_fetch_runs_with_fastqs_and_links():
    f, _ = fetcher(_route("SRP400001", json.dumps(ENA_RUNS)))
    records = ena.fetch("SRP400001", f)
    first = records[0]
    assert (first.repo, first.record_id, first.kind) == ("ena", "SRR3000001", "run")
    assert first.links == ["SRX2000001", "SAMN1", "SRS1", "PRJNA100001", "SRP400001"]
    assert first.fields["library_strategy"] == "RNA-Seq"
    assert "fastq_ftp" not in first.fields and "library_name" not in first.fields
    assert [(x.name, x.url, x.size, x.checksum, x.checksum_type) for x in first.files] == [
        ("SRR3000001_1.fastq.gz", "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR300/001/SRR3000001/SRR3000001_1.fastq.gz", 100, "aa", "md5"),
        ("SRR3000001_2.fastq.gz", "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR300/001/SRR3000001/SRR3000001_2.fastq.gz", 120, "bb", "md5"),
    ]
    assert first.submitted == date(2024, 3, 1)
    assert records[2].files == []


def test_empty_body_is_no_records():
    f, _ = fetcher(_route("SRP1", ""))
    assert ena.fetch("SRP1", f) == []
