"""Collect FULL free-text protocol descriptions from a cohort's lineage.

NExtSEEK's `Protocol` field holds either a SOP URL or free-form prose. The
shipped digest path only handles the URL case: `extract_protocol_refs_from_metadata`
looks for links, downloads the SOP's attachments and extracts their text. Prose
in the `Protocol` field is not a document, so it never becomes a protocol — it
survives only as an ordinary metadata field.

That is a problem, because `build_metadata_summary` truncates every field value
to 120 characters (`_scalar_for_summary`) and keeps at most 3 examples. On the
team's production cohorts the single most decisive piece of evidence is prose
on the `DNA` record — e.g. "rRNA-depleted total RNA library prep (NEB Ultra II
Ribosomal RNA Depletion for Human/Mouse...)" — and 120 characters cuts it
mid-sentence, sometimes before the part that discriminates between pipelines.

So this module gathers the DISTINCT, UNTRUNCATED `Protocol` strings across the
whole retrieved lineage and attaches them to the digest as their own section.
It does not invent protocols: if a cohort's `Protocol` fields are all empty,
it attaches nothing and says so.
"""
from __future__ import annotations

import re
from typing import Any

from chat_nextseek.reports.metadata import fetch_reporter_metadata

URLISH = re.compile(r"https?://", re.I)

#: Fields carrying prose worth keeping alongside Protocol. These are the other
#: places the team's records describe how the library was made; each is only
#: included when it is populated.
COMPANION_FIELDS = ("LibraryPrep", "Notes", "Description", "Method")


def collect_protocol_prose(config, uids: list[str]) -> dict[str, Any]:
    """Return distinct full-length free-text Protocol values per sample type
    for `uids` and their whole retrieved lineage.

    Shape:
        {"by_sample_type": {"DNA": ["rRNA-depleted total RNA library prep ..."]},
         "n_free_text": int, "n_sop_urls": int, "n_empty": int}
    """
    metadata = fetch_reporter_metadata(config, uids)
    if not isinstance(metadata, dict) or not metadata.get("ok"):
        return {"by_sample_type": {}, "n_free_text": 0, "n_sop_urls": 0, "n_empty": 0,
                "error": (metadata or {}).get("error", "metadata fetch failed")}

    by_type: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    n_free = n_url = n_empty = 0

    for blk in ((metadata.get("data") or {}).get("data") or []):
        st = blk.get("sample_type") or "unknown"
        for sample in blk.get("samples") or []:
            md = sample.get("metadata") or {}
            value = md.get("Protocol")
            if value is None or (isinstance(value, str) and not value.strip()):
                n_empty += 1
                continue
            text = str(value).strip()
            if URLISH.search(text):
                # A URL is handled by the real protocol machinery; counting it
                # here as prose would double-count the same evidence.
                n_url += 1
                continue
            n_free += 1
            if text not in seen.setdefault(st, set()):
                seen[st].add(text)
                by_type.setdefault(st, []).append(text)

    return {
        "by_sample_type": {k: sorted(v) for k, v in sorted(by_type.items())},
        "n_free_text": n_free,
        "n_sop_urls": n_url,
        "n_empty": n_empty,
    }


def attach_protocol_prose(digest: dict[str, Any], prose: dict[str, Any]) -> dict[str, Any]:
    """Add a `protocol_prose` section to a digest, in place, and return it.

    Placed as the first key for the same reason fdh_digest leads with its
    availability block: the digest is serialized whole into the prompt, and
    evidence buried after the metadata summary is evidence the model does not
    reliably act on.
    """
    n = prose.get("n_free_text", 0)
    if n:
        note = (
            f"{n} sample record(s) across this cohort's lineage carry a free-text "
            "`Protocol` description rather than a link to a SOP document. The distinct "
            "values are reproduced BELOW IN FULL — the copies inside metadata_summary are "
            "truncated to 120 characters and must not be relied on. Treat these as the "
            "cohort's protocol evidence: they describe the library preparation and "
            "sequencing, and the DNA/RNA-level entries are usually the ones that name the "
            "library prep kit."
        )
    else:
        note = (
            "No protocol evidence of any kind: every `Protocol` field across this cohort's "
            "lineage is empty, and no SOP document is linked. Judge the library type from "
            "the sample metadata fields (LibraryStrategy, LibrarySource, LibrarySelection, "
            "SequencingType, DataType, F_bp/R_bp) and say in your reasoning that no "
            "protocol was available."
        )
    section = {"note": note, **prose}
    return {"protocol_prose": section, **digest}
