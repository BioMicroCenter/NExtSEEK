import io
import warnings
import zipfile

from openpyxl import Workbook

from NessieAI.ns.paper.evidence import deposit_items, paper_items, record_evidence_ids
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import DepositRecord, FileRef, Gap, SupplementRef
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


def test_duplicate_file_names_in_one_record_get_distinct_ids():
    rec = DepositRecord(repo="geo", record_id="GSM1", kind="sample", files=[
        FileRef(name="a.gz", url="ftp://x/1/a.gz"), FileRef(name="a.gz", url="ftp://x/2/a.gz")])
    items = {i.id: i.text for i in deposit_items(rec)}
    assert items == {"E:file:geo:GSM1:a.gz": "a.gz ftp://x/1/a.gz",
                     "E:file:geo:GSM1:a.gz#2": "a.gz ftp://x/2/a.gz"}
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


def test_same_basename_in_two_folders_keeps_ids_unique():
    data = _zip({"a/TableS1.csv": b"first\n", "b/TableS1.csv": b"second\n"})
    items, _ = supplement_items(data, [])
    got = {i.id: i.text for i in items}
    assert got == {"E:si:TableS1.csv!A1": "first", "E:si:b/TableS1.csv!A1": "second"}


def test_unreadable_archive_becomes_gaps_not_a_failure():
    items, gaps = supplement_items(b"not a zip", [SupplementRef(name="TableS1.xlsx")])
    assert items == []
    assert gaps == [Gap(kind="supplement_unparsed", detail="supplementary archive: BadZipFile"),
                    Gap(kind="supplement_unavailable", detail="TableS1.xlsx")]


def test_corrupt_member_is_a_gap_naming_the_file():
    items, gaps = supplement_items(_zip({"bad.xlsx": b"garbage bytes"}), [])
    assert items == []
    assert [(g.kind, g.detail.split(":")[0]) for g in gaps] == [("supplement_unparsed", "bad.xlsx")]


def test_empty_readable_file_is_a_gap():
    items, gaps = supplement_items(_zip({"empty.csv": b""}), [])
    assert items == []
    assert gaps == [Gap(kind="supplement_unparsed", detail="empty.csv: no readable text")]


def test_tsv_cells_get_cell_ids():
    items, gaps = supplement_items(_zip({"TableS3.tsv": b"id\tdose\nM2\t7 mg\n"}), [])
    assert gaps == []
    assert {i.id: i.text for i in items}["E:si:TableS3.tsv!B2"] == "7 mg"


def test_member_order_never_duplicates_a_stem():
    items, _ = supplement_items(_zip({"a/T.csv": b"first\n", "T.csv": b"second\n"}), [])
    assert sorted((i.id, i.text) for i in items) == [("E:si:T.csv!A1", "first"), ("E:si:T.csv#2!A1", "second")]


def test_exact_duplicate_path_stays_unique():
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns on a repeated name; the archive is the test input
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a/T.csv", b"one\n")
            zf.writestr("a/T.csv", b"two\n")
    items, _ = supplement_items(buf.getvalue(), [])
    assert sorted(i.id for i in items) == ["E:si:T.csv!A1", "E:si:a/T.csv!A1"]
