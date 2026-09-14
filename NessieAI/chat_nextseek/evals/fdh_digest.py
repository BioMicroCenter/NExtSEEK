"""Build a pipeline-selection digest from FAIRDOM SEEK sample records instead
of the NExtSEEK API.

The team's samples are not on any NExtSEEK instance — they live in SEEK on
`fairdata.mit.edu`, where each sample carries its NExtSEEK fields in
`attributes.attribute_map`. `build_sample_digest` cannot reach them: it starts
from `fetch_reporter_metadata`, which speaks to the NExtSEEK API.

So this module replaces the FETCH ONLY. It reshapes SEEK records into the
payload `fetch_reporter_metadata` would have returned, then runs the real
`annotate_metadata_with_sampletypes` -> `build_metadata_summary` ->
`filter_summary_for_deg` chain over it. The summary and grouping candidates
are therefore produced by shipped code, not re-derived here — if those change,
this follows automatically.

## Two honest differences from a NExtSEEK digest

**No protocol documents.** In NExtSEEK a sample's `Protocol` field is a URL to
a SOP whose attachments get downloaded and text-extracted. On these SEEK
records `Protocol` is free text (e.g. "NovaSeq X 25B, 300-cycle flowcell,
2x150 paired-end"), so there is no document to fetch and `protocols` is
legitimately empty — nothing failed.

That creates a trap. `selection_context` only emits its PROTOCOL TEXT
UNAVAILABLE notice when `n_failed > 0`; a cohort with zero protocols has
`n_failed == 0`, so the model would get no protocol text AND no warning — the
same silent condition that flipped cohort 241219BRY to the wrong answer. This
module therefore writes an explicit `protocol_availability` block as the FIRST
key of the digest, naming the fallback evidence. `protocol_text_status` is
left factually accurate (0/0/0) rather than faked into firing the built-in
notice.

**D.SEQ-level lineage only.** A NExtSEEK digest carries the whole family
(TIS -> CEL -> DNA -> D.SEQ ...). Here only the sample types named by the
requested UIDs were indexed, so a `Parent` pointing at e.g. `DNA-260527WHI-1`
has no indexed parent record and contributes no lineage edge. Grouping
candidates come from D.SEQ-level fields (Region, Timepoint, Notes, ...) only.
`lineage_note` records this in the digest so it is visible rather than
inferred from an empty list.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chat_nextseek.reports.metadata import (
    annotate_metadata_with_sampletypes,
    build_metadata_summary,
    filter_summary_for_deg,
)

#: Fields the model should fall back to when there is no protocol text. Same
#: list selection_context names in its own notice, plus DataType/LibraryDesign,
#: which these SEEK records carry and NExtSEEK's D.SEQ records generally do not.
FALLBACK_FIELDS = (
    "LibraryStrategy", "LibrarySource", "LibrarySelection", "SequencingType",
    "DataType", "LibraryDesign", "F_bp", "R_bp",
)

PROTOCOL_NOTE = (
    "No protocol documents exist for this cohort. These samples come from FAIRDOM "
    "SEEK (fairdata.mit.edu), where a sample's `Protocol` field is free-form text "
    "describing the run rather than a link to a SOP document, so there is nothing "
    "to download or extract — this is an absence of documents, not a failed fetch. "
    "Do not infer the library preparation from a protocol title or filename. Judge "
    "the library type from the sample metadata fields instead — "
    + ", ".join(FALLBACK_FIELDS)
    + " — together with the free-text `Protocol` values shown in the metadata "
    "summary, and say in your reasoning that no protocol document was available."
)

LINEAGE_NOTE = (
    "Only the sample types named by the requested UIDs were indexed on this "
    "instance, so a sample's `Parent` may point at a record that is not present "
    "here. Absent lineage edges mean 'not indexed', NOT 'no parent exists'. "
    "Grouping candidates below are drawn from the requested samples' own fields."
)


def seek_records_to_metadata_payload(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Reshape `{uid: {sample_id, sample_type, title, attribute_map}}` into the
    payload `fetch_reporter_metadata` returns, so shipped summary code can walk
    it unchanged.

    Target shape (see reports.metadata._entries_from_metadata):
        {"ok": True, "status_code": 200,
         "data": {"total_samples": N, "total_sample_types": M,
                  "data": [{"sample_type": ST, "n_samples": n, "samples": [...]}]}}
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for uid, rec in sorted(records.items()):
        st = rec.get("sample_type") or (uid.split("-")[0] if "-" in uid else "unknown")
        by_type.setdefault(st, []).append({
            "id": rec.get("sample_id"),
            "uuid": uid,
            "metadata": rec.get("attribute_map") or {},
        })

    blocks = [
        {"sample_type": st, "n_samples": len(samples), "samples": samples}
        for st, samples in sorted(by_type.items())
    ]
    return {
        "ok": True,
        "status_code": 200,
        "data": {
            "total_samples": sum(len(b["samples"]) for b in blocks),
            "total_sample_types": len(blocks),
            "data": blocks,
        },
    }


def build_seek_digest(config, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a digest with the same shape `build_sample_digest` returns, built
    from SEEK records rather than the NExtSEEK API.

    `protocol_availability` comes first deliberately: the digest is serialized
    wholesale into the prompt, and a caveat buried after the metadata summary
    is a caveat the model does not act on.
    """
    if not records:
        raise ValueError("Cannot build a digest from zero sample records")

    payload = seek_records_to_metadata_payload(records)
    annotated = annotate_metadata_with_sampletypes(config, payload)

    summary = build_metadata_summary({"__sample__": annotated}) or {}
    grouping = filter_summary_for_deg(summary) or {}
    summary = {k: v for k, v in summary.items() if k != "_uid_index"}
    grouping = {k: v for k, v in grouping.items() if k != "_uid_index"}

    return {
        "protocol_availability": {"n_protocol_documents": 0, "note": PROTOCOL_NOTE},
        "source": "FAIRDOM SEEK (fairdata.mit.edu) sample attribute_map",
        "n_uids": len(records),
        "lineage_note": LINEAGE_NOTE,
        "metadata_summary": summary,
        "grouping_candidates": grouping,
        "protocols": {},
        "protocol_text_status": {
            "n_protocols": 0, "n_ok": 0, "n_failed": 0, "failure_reasons": [],
        },
    }


def load_question_samples(path: str | Path) -> list[dict[str, Any]]:
    """Load the per-question record file written by ns-published-fdh's
    pull_team_uids.py: a list of
    {key, question, n_uids, n_resolved, missing, samples:{uid: record}}.
    """
    return json.loads(Path(path).read_text())
