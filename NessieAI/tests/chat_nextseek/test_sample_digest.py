"""build_sample_digest: profile a cohort without knowing which pipeline it is for."""
from pathlib import Path

import pytest

from chat_nextseek.pipeline.sample_digest import DigestError, build_sample_digest


def _deps(**overrides):
    """Collaborators stubbed to plausible real shapes; override per test."""
    base = {
        "fetch_metadata": lambda config, uids: {
            "ok": True,
            "data": {"data": [{"sample_type": "D.SEQ", "samples": [
                {"uuid": "D.SEQ-1", "metadata": {
                    "UID": "D.SEQ-1", "LibraryStrategy": "RNA-Seq",
                    "Treatment": "vehicle", "Protocol": "P.RNAprep",
                }},
                {"uuid": "D.SEQ-2", "metadata": {
                    "UID": "D.SEQ-2", "LibraryStrategy": "RNA-Seq",
                    "Treatment": "drug", "Protocol": "P.RNAprep",
                }},
            ]}]},
        },
        "annotate": lambda config, metadata: metadata,
        "summarise": lambda metadata_map: {
            "by_sample_type": {"D.SEQ": {"n_samples": 2, "fields": {
                "Treatment": {"n_populated": 2, "n_distinct": 2, "examples": ["vehicle", "drug"]},
                "UID": {"n_populated": 2, "n_distinct": 2, "examples": ["D.SEQ-1", "D.SEQ-2"]},
            }}},
            "lineage_edges": ["TIS → D.SEQ"],
            "_uid_index": {"D.SEQ-1": {"metadata": {"enormous": "payload"}}},
        },
        "filter_deg": lambda summary: {"by_sample_type": {"D.SEQ": {"fields": {
            "Treatment": {"n_distinct": 2, "examples": ["vehicle", "drug"]},
        }}}},
        "extract_refs": lambda metadata: [{"source": "protocol_name", "value": "P.RNAprep", "raw": "P.RNAprep"}],
        "fetch_protocols": lambda config, refs: {"P.RNAprep": {"ok": True, "title": "RNA prep"}},
        "download_blobs": lambda payloads, base_dir, config=None, token_limit=None: {
            "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                           "ok": True, "text": "TruSeq Stranded mRNA library prep."}],
        },
        "sanitize": lambda payloads: payloads,
    }
    base.update(overrides)
    return base


def test_raises_when_no_uids_given():
    with pytest.raises(DigestError, match="no sample UIDs"):
        build_sample_digest(config=None, uids=[], deps=_deps())


def test_raises_when_the_metadata_fetch_fails():
    deps = _deps(fetch_metadata=lambda config, uids: {"ok": False, "error": "connection refused"})
    with pytest.raises(DigestError, match="connection refused"):
        build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)


def test_drops_uid_index_from_the_summary():
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=_deps())
    assert "_uid_index" not in out["metadata_summary"]
    assert out["metadata_summary"]["by_sample_type"]["D.SEQ"]["n_samples"] == 2


def test_keeps_grouping_candidates_for_the_tie_between_rnaseq_and_rnasplice():
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=_deps())
    fields = out["grouping_candidates"]["by_sample_type"]["D.SEQ"]["fields"]
    assert "Treatment" in fields


def test_includes_full_protocol_text():
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=_deps())
    attachment = out["protocols"]["P.RNAprep"]["attachments"][0]
    assert attachment["text"] == "TruSeq Stranded mRNA library prep."
    assert attachment["extraction"] == "ok"


def test_download_blobs_is_called_with_token_limit_none():
    """The selection path needs uncapped protocol text, so build_sample_digest
    must pass token_limit=None through to its download_blobs dependency."""
    received = {}

    def recording_download_blobs(payloads, base_dir, config=None, token_limit="unset"):
        received["token_limit"] = token_limit
        return {"P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                                "ok": True, "text": "x"}]}

    deps = _deps(download_blobs=recording_download_blobs)
    build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)
    assert received["token_limit"] is None


def test_truncated_attachment_surfaces_the_flag_in_the_digest():
    deps = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
        "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                       "ok": True, "text": "partial text...", "text_truncated": True}],
    })
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)
    attachment = out["protocols"]["P.RNAprep"]["attachments"][0]
    assert attachment["text_truncated"] is True


def test_untruncated_attachment_defaults_text_truncated_to_false():
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=_deps())
    attachment = out["protocols"]["P.RNAprep"]["attachments"][0]
    assert attachment["text_truncated"] is False


def test_distinguishes_extraction_unavailable_from_no_attachments():
    deps = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
        "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                       "ok": True, "text": None}],
    })
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps,
                              pdf_available=False)
    assert out["protocols"]["P.RNAprep"]["attachments"][0]["extraction"] == \
        "unavailable: PyPDF2 not importable"

    deps_empty = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {"P.RNAprep": []})
    out2 = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps_empty)
    assert out2["protocols"]["P.RNAprep"]["attachments"] == []


def test_reports_extraction_failure_when_the_library_is_present():
    deps = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
        "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                       "ok": True, "text": None}],
    })
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps, pdf_available=True)
    assert out["protocols"]["P.RNAprep"]["attachments"][0]["extraction"] == \
        "failed: no text extracted"


def test_download_runs_on_raw_payloads_but_returned_payload_is_sanitized():
    """download_and_extract_protocol_blobs must see the RAW fetch_protocols
    output (its own localhost fixup depends on source_base_url, which
    sanitize_protocols_for_llm's unconditional rewrite would already have
    erased). Only the stored payload should be sanitized."""
    raw_payload = {"ok": True, "title": "RNA prep", "source_base_url": "https://fairdomhub.org"}
    sanitized_payload = {"ok": True, "title": "RNA prep [sanitized]", "source_base_url": "https://fairdomhub.org"}
    received_by_download = {}

    def fake_download_blobs(payloads, base_dir, config=None, token_limit=None):
        received_by_download.update(payloads)
        return {"P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                                "ok": True, "text": "TruSeq Stranded mRNA library prep."}]}

    def fake_sanitize(payloads):
        assert payloads == {"P.RNAprep": raw_payload}
        return {"P.RNAprep": sanitized_payload}

    deps = _deps(
        fetch_protocols=lambda config, refs: {"P.RNAprep": raw_payload},
        download_blobs=fake_download_blobs,
        sanitize=fake_sanitize,
    )
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)

    assert received_by_download == {"P.RNAprep": raw_payload}
    assert out["protocols"]["P.RNAprep"]["payload"] == sanitized_payload


def test_fallback_temp_dir_is_removed_but_explicit_base_dir_is_kept(tmp_path):
    seen_dirs = []

    def recording_download_blobs(payloads, base_dir, config=None, token_limit=None):
        seen_dirs.append(Path(base_dir))
        return {"P.RNAprep": []}

    deps = _deps(download_blobs=recording_download_blobs)

    build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)
    assert len(seen_dirs) == 1
    assert not seen_dirs[0].exists(), "fallback temp dir must be cleaned up after use"

    explicit_dir = tmp_path / "session-artifacts"
    explicit_dir.mkdir()
    build_sample_digest(config=None, uids=["D.SEQ-1"], base_dir=explicit_dir, deps=deps)
    assert seen_dirs[1] == explicit_dir
    assert explicit_dir.exists(), "caller-supplied base_dir must not be deleted"


def test_protocol_text_status_all_ok():
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=_deps())
    assert out["protocol_text_status"] == {
        "n_protocols": 1,
        "n_ok": 1,
        "n_failed": 0,
        "failure_reasons": [],
    }


def test_protocol_text_status_all_failed():
    deps = _deps(
        fetch_protocols=lambda config, refs: {
            "P.RNAprep": {"ok": True, "title": "RNA prep"},
            "P.Other": {"ok": True, "title": "Other prep"},
        },
        extract_refs=lambda metadata: [
            {"source": "protocol_name", "value": "P.RNAprep", "raw": "P.RNAprep"},
            {"source": "protocol_name", "value": "P.Other", "raw": "P.Other"},
        ],
        download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
            "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                           "ok": True, "text": None}],
            "P.Other": [{"filename": "other.pdf", "content_type": "application/pdf",
                         "ok": True, "text": None}],
        },
        sanitize=lambda payloads: payloads,
    )
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps, pdf_available=True)
    assert out["protocol_text_status"] == {
        "n_protocols": 2,
        "n_ok": 0,
        "n_failed": 2,
        "failure_reasons": ["failed: no text extracted"],
    }


def test_protocol_text_status_mixed():
    deps = _deps(
        fetch_protocols=lambda config, refs: {
            "P.RNAprep": {"ok": True, "title": "RNA prep"},
            "P.Other": {"ok": True, "title": "Other prep"},
        },
        extract_refs=lambda metadata: [
            {"source": "protocol_name", "value": "P.RNAprep", "raw": "P.RNAprep"},
            {"source": "protocol_name", "value": "P.Other", "raw": "P.Other"},
        ],
        download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
            "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                           "ok": True, "text": "TruSeq Stranded mRNA library prep."}],
            "P.Other": [{"filename": "other.pdf", "content_type": "application/pdf",
                         "ok": False, "error": "download failed"}],
        },
        sanitize=lambda payloads: payloads,
    )
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)
    assert out["protocol_text_status"] == {
        "n_protocols": 2,
        "n_ok": 1,
        "n_failed": 1,
        "failure_reasons": ["failed: download failed"],
    }


def test_protocol_text_status_when_there_are_no_protocols_at_all():
    """No protocol references at all (e.g. 240910LAU) is not itself a
    failure — nothing was expected to be found, so nothing failed."""
    deps = _deps(extract_refs=lambda metadata: [], fetch_protocols=lambda config, refs: {})
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps)
    assert out["protocol_text_status"] == {
        "n_protocols": 0,
        "n_ok": 0,
        "n_failed": 0,
        "failure_reasons": [],
    }


def test_docx_extraction_failure_is_not_misattributed_to_pypdf2():
    deps = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
        "P.RNAprep": [{"filename": "prep.docx",
                       "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       "ok": True, "text": None}],
    })
    out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=deps, pdf_available=False)
    extraction = out["protocols"]["P.RNAprep"]["attachments"][0]["extraction"]
    assert extraction != "unavailable: PyPDF2 not importable"
    assert extraction == "failed: no text extracted"

    pdf_deps = _deps(download_blobs=lambda payloads, base_dir, config=None, token_limit=None: {
        "P.RNAprep": [{"filename": "prep.pdf", "content_type": "application/pdf",
                       "ok": True, "text": None}],
    })
    pdf_out = build_sample_digest(config=None, uids=["D.SEQ-1"], deps=pdf_deps, pdf_available=False)
    assert pdf_out["protocols"]["P.RNAprep"]["attachments"][0]["extraction"] == \
        "unavailable: PyPDF2 not importable"
