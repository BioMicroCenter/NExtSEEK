"""Evidence items (spec §4.1): every quotable thing, with a stable ID and its exact text."""
from __future__ import annotations

from NessieAI.ns.paper.models import DepositRecord, EvidenceItem, FileRef, PaperText


def paper_items(text: PaperText) -> list[EvidenceItem]:
    items = [EvidenceItem(id=p.id, text=p.text, source=f"paper, {s.title or s.slug}")
             for s in text.sections for p in s.paragraphs]
    return items + [EvidenceItem(id=c.id, text=c.text, source="paper, figure or table caption") for c in text.captions]


def field_id(rec: DepositRecord, field: str) -> str:
    return f"E:{rec.repo}:{rec.record_id}:{field}"


def file_id(rec: DepositRecord, ref: FileRef) -> str:
    return f"E:file:{rec.repo}:{rec.record_id}:{ref.name}"


def deposit_items(rec: DepositRecord) -> list[EvidenceItem]:
    where = f"{rec.repo.upper()} {rec.record_id}"
    items = [EvidenceItem(id=field_id(rec, k), text=v, source=f"{where}, field {k}") for k, v in rec.fields.items()]
    if rec.title and "title" not in rec.fields:
        items.append(EvidenceItem(id=field_id(rec, "title"), text=rec.title, source=f"{where}, title"))
    counts: dict[str, int] = {}
    for ref in rec.files:
        counts[ref.name] = counts.get(ref.name, 0) + 1
        suffix = "" if counts[ref.name] == 1 else f"#{counts[ref.name]}"
        bits = [ref.name, ref.url]
        if ref.size is not None:
            bits.append(f"size={ref.size}")
        if ref.checksum:
            bits.append(f"{ref.checksum_type}={ref.checksum}")
        items.append(EvidenceItem(id=file_id(rec, ref) + suffix, text=" ".join(bits), source=f"{where}, file"))
    return items


def record_evidence_ids(rec: DepositRecord) -> list[str]:
    return [item.id for item in deposit_items(rec)]
