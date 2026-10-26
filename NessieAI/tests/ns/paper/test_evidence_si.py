import io
import zipfile

from openpyxl import Workbook

from NessieAI.ns.paper.evidence import deposit_items, paper_items, record_evidence_ids
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import DepositRecord, FileRef, SupplementRef
from NessieAI.ns.paper.si import supplement_items
from NessieAI.tests.ns.paper.fixtures import JATS_XML


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Samples"
    ws.append(["Mouse", "Sex"])
    ws.append(["M1", "female"])
    ws["D9"] = None
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_paper_items_cover_paragraphs_and_captions():
    ids = [i.id for i in paper_items(parse_jats(JATS_XML))]
    assert "E:methods:p1" in ids and "E:cap:1" in ids


def test_deposit_items_and_ids():
    rec = DepositRecord(repo="geo", record_id="GSM1", kind="sample", title="t1",
                        fields={"characteristics.sex": "female"},
                        files=[FileRef(name="a.gz", url="ftp://x/a.gz", size=3, checksum="ab", checksum_type="md5")])
    items = {i.id: i.text for i in deposit_items(rec)}
    assert items == {"E:geo:GSM1:characteristics.sex": "female", "E:geo:GSM1:title": "t1",
                     "E:file:geo:GSM1:a.gz": "a.gz ftp://x/a.gz size=3 md5=ab"}
    assert record_evidence_ids(rec) == list(items)


def test_supplement_items_reads_tables_and_reports_gaps():
    data = _zip({"suppl/NIHMS1_TableS1.xlsx": _xlsx(), "TableS2.csv": b"id,dose\nM1,5 mg\n",
                 "methods.docx": b"PK..", "notes.txt": b"First note.\n\nSecond note.\n"})
    items, gaps = supplement_items(data, [SupplementRef(name="NIHMS1_TableS1.xlsx"), SupplementRef(name="MovieS1.mp4")])
    got = {i.id: i.text for i in items}
    assert got["E:si:NIHMS1_TableS1.xlsx!Samples!B2"] == "female"
    assert got["E:si:TableS2.csv!B2"] == "5 mg"
    assert got["E:si:notes.txt!p2"] == "Second note."
    assert {(g.kind, g.detail.split(":")[0]) for g in gaps} == {
        ("supplement_unparsed", "methods.docx"), ("supplement_unavailable", "MovieS1.mp4")}
