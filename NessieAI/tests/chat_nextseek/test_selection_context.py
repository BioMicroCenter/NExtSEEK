"""build_selection_context: assemble atlas + schemas + digest into one payload."""
import pytest

from chat_nextseek.pipeline.selection_context import (
    RICH_PIPELINES,
    PayloadTooLargeError,
    build_selection_context,
)
from chat_nextseek.seqera.nfcore_schema import SchemaFetchError

ATLAS = {
    "guidance": {"ties_are_acceptable": True, "notes": ["ties are fine"]},
    "pipelines": {
        "rnaseq": {"revision": "3.18.0", "answers": "expression",
                   "versus": {"rnasplice": {"differs_by": "isoforms vs totals",
                                            "ask_user": "groups or overall?"}}},
        "rnasplice": {"revision": "1.0.4", "answers": "isoform shifts",
                      "versus": {"rnaseq": {"differs_by": "totals vs isoforms"}}},
        # A non-rich stub pipeline: present in the atlas but not in
        # RICH_PIPELINES, so it must never get a schema fetched for it.
        "rnavar": {"revision": "1.0.0", "answers": "variants",
                   "versus": {"rnaseq": {"differs_by": "variants vs expression"}}},
    },
}

DIGEST = {
    "n_uids": 2,
    "metadata_summary": {"by_sample_type": {"D.SEQ": {"n_samples": 2, "fields": {}}}},
    "grouping_candidates": {"by_sample_type": {"D.SEQ": {"fields": {"Treatment": {}}}}},
    "protocols": {"P.RNAprep": {"payload": {}, "attachments": []}},
}


def _schema_getter(fail_for=()):
    def get(pipeline, revision):
        if pipeline in fail_for:
            raise SchemaFetchError(f"{pipeline}@{revision}: read timeout")
        return {"nextflow_schema": {"title": f"{pipeline} params"},
                "input_schema": {"title": f"{pipeline} columns"}}
    return get


def _docs_getter(fail_for=()):
    def get(pipeline, revision):
        if pipeline in fail_for:
            raise SchemaFetchError(f"{pipeline}@{revision}: read timeout")
        return {"readme": f"# {pipeline}\nAn overview of {pipeline}.",
                "usage": f"## Usage\nHow to run {pipeline}.",
                "output": f"## Output\nWhat {pipeline} produces."}
    return get


def _build(**kwargs):
    return build_selection_context(
        config=None, uids=["D.SEQ-1", "D.SEQ-2"],
        atlas=ATLAS, digest=DIGEST,
        schema_getter=kwargs.pop("schema_getter", _schema_getter()),
        docs_getter=kwargs.pop("docs_getter", _docs_getter()),
        **kwargs,
    )


def test_the_six_rich_pipelines_are_the_ones_that_get_schemas():
    assert RICH_PIPELINES == (
        "rnaseq", "scrnaseq", "smrnaseq", "hlatyping", "rnafusion", "rnasplice",
    )


def test_fetches_schemas_only_for_rich_pipelines_present_in_the_atlas():
    ctx = _build()
    assert set(ctx.schemas) == {"rnaseq", "rnasplice"}
    # rnavar is in the atlas but is not one of RICH_PIPELINES: exclusion must
    # hold because it's filtered, not merely because no test asked for it.
    assert "rnavar" not in ctx.schemas


def test_a_failed_schema_fetch_is_recorded_not_swallowed():
    ctx = _build(schema_getter=_schema_getter(fail_for={"rnasplice"}))
    assert set(ctx.schemas) == {"rnaseq"}
    assert "read timeout" in ctx.schema_fetch_failed["rnasplice"]
    # the model must be told which pipeline it is judging blind
    assert "read timeout" in ctx.to_prompt_text()


def test_failed_schema_notice_appears_before_the_json_body():
    """A fetch failure must not land buried in ~127k chars of compact JSON —
    the plain-text notice belongs before the JSON, not merely present in it."""
    ctx = _build(schema_getter=_schema_getter(fail_for={"rnasplice"}))
    text = ctx.to_prompt_text()
    # Scope the search to the schemas section: the docs section immediately
    # before it also renders a `{"fetched"...}` body (with no failures here),
    # so an unscoped index() would find that one instead.
    schemas_section_start = text.index("## NF-CORE SCHEMAS")
    notice_index = text.index("NOT FETCHED", schemas_section_start)
    json_body_index = text.index('{"fetched"', schemas_section_start)
    assert notice_index < json_body_index
    assert text.index("rnasplice", notice_index) < json_body_index


def test_no_failure_notice_when_every_schema_fetch_succeeds():
    ctx = _build()
    assert "NOT FETCHED" not in ctx.to_prompt_text()


def test_prompt_orders_atlas_then_digest_then_docs_then_schemas():
    text = _build().to_prompt_text()
    assert text.index("## PIPELINE ATLAS") < text.index("## SAMPLE DIGEST")
    assert text.index("## SAMPLE DIGEST") < text.index("## NF-CORE PIPELINE DOCS")
    assert text.index("## NF-CORE PIPELINE DOCS") < text.index("## NF-CORE SCHEMAS")


def test_prompt_carries_the_tie_guidance_and_the_ask_user_question():
    text = _build().to_prompt_text()
    assert "ties are fine" in text
    assert "groups or overall?" in text


def test_size_report_breaks_the_payload_down_by_section():
    ctx = _build()
    report = ctx.size_report()
    assert set(report) >= {"atlas", "digest", "docs", "schemas", "total_chars", "est_tokens"}
    assert report["docs"] > 0
    assert report["total_chars"] > 0
    assert report["est_tokens"] == report["total_chars"] // 4
    # total_chars must reflect the actual assembled prompt, not just be
    # internally self-consistent with est_tokens.
    assert report["total_chars"] == len(ctx.to_prompt_text())


def test_fetches_docs_only_for_rich_pipelines_present_in_the_atlas():
    ctx = _build()
    assert set(ctx.docs) == {"rnaseq", "rnasplice"}
    assert "rnavar" not in ctx.docs


def test_a_failed_docs_fetch_is_recorded_not_swallowed():
    ctx = _build(docs_getter=_docs_getter(fail_for={"rnasplice"}))
    assert set(ctx.docs) == {"rnaseq"}
    assert "read timeout" in ctx.docs_fetch_failed["rnasplice"]
    assert "read timeout" in ctx.to_prompt_text()


def test_failed_docs_notice_appears_before_the_docs_json_body():
    ctx = _build(docs_getter=_docs_getter(fail_for={"rnasplice"}))
    text = ctx.to_prompt_text()
    notice_index = text.index("NOT FETCHED — these pipelines were judged without their documentation")
    docs_json_index = text.index('{"fetched"', notice_index)
    assert notice_index < docs_json_index
    assert text.index("rnasplice", notice_index) < docs_json_index


def test_no_docs_failure_notice_when_every_docs_fetch_succeeds():
    ctx = _build()
    assert "judged without their documentation" not in ctx.to_prompt_text()


def test_prompt_carries_the_pipeline_docs_content():
    text = _build().to_prompt_text()
    assert "An overview of rnaseq" in text
    assert "How to run rnasplice" in text


def _build_with_digest(digest):
    return build_selection_context(
        config=None, uids=["D.SEQ-1", "D.SEQ-2"],
        atlas=ATLAS, digest=digest,
        schema_getter=_schema_getter(), docs_getter=_docs_getter(),
    )


def test_protocol_unavailable_notice_appears_before_the_digest_json_body():
    digest = {**DIGEST, "protocol_text_status": {
        "n_protocols": 2, "n_ok": 0, "n_failed": 2,
        "failure_reasons": ["failed: download failed"],
    }}
    ctx = _build_with_digest(digest)
    text = ctx.to_prompt_text()
    digest_section_start = text.index("## SAMPLE DIGEST")
    notice_index = text.index("PROTOCOL TEXT UNAVAILABLE", digest_section_start)
    json_body_index = text.index('"n_uids"', digest_section_start)
    assert notice_index < json_body_index
    assert "2 of 2 protocols" in text[notice_index:json_body_index]
    assert "failed: download failed" in text[notice_index:json_body_index]
    assert "D.SEQ metadata" in text[notice_index:json_body_index]
    assert "LibraryStrategy" in text[notice_index:json_body_index]


def test_no_protocol_notice_when_every_protocol_yields_text():
    digest = {**DIGEST, "protocol_text_status": {
        "n_protocols": 1, "n_ok": 1, "n_failed": 0, "failure_reasons": [],
    }}
    ctx = _build_with_digest(digest)
    assert "PROTOCOL TEXT UNAVAILABLE" not in ctx.to_prompt_text()


def test_no_protocol_notice_when_the_digest_has_no_protocol_text_status_key():
    """Digests built before this field existed (or passed in directly by a
    caller/test that predates it) must not crash size_report or to_prompt_text."""
    ctx = _build_with_digest(DIGEST)
    assert "PROTOCOL TEXT UNAVAILABLE" not in ctx.to_prompt_text()


def test_size_report_total_chars_holds_with_the_protocol_notice_present():
    digest = {**DIGEST, "protocol_text_status": {
        "n_protocols": 2, "n_ok": 0, "n_failed": 2,
        "failure_reasons": ["failed: download failed"],
    }}
    ctx = _build_with_digest(digest)
    report = ctx.size_report()
    assert report["total_chars"] == len(ctx.to_prompt_text())
    assert report["digest"] == len(ctx._digest_text())


def test_raises_with_a_breakdown_when_the_payload_exceeds_the_ceiling():
    with pytest.raises(PayloadTooLargeError) as exc:
        _build(max_tokens=1)
    message = str(exc.value)
    assert "est_tokens" in message and "schemas" in message


# ---------------------------------------------------------------------------
# Section selection — for measuring what each part of the payload is worth
# ---------------------------------------------------------------------------


def test_default_assembles_all_four_sections_in_reasoning_order():
    text = _build().to_prompt_text()
    positions = [text.index(h) for h in (
        "## PIPELINE ATLAS", "## SAMPLE DIGEST",
        "## NF-CORE PIPELINE DOCS", "## NF-CORE SCHEMAS",
    )]
    assert positions == sorted(positions)


def test_a_subset_omits_the_other_sections_entirely():
    """Omitted, not blanked: an empty {"fetched":{},"failed":{}} body would read
    as "the docs were fetched and are empty" rather than "you got no docs"."""
    text = _build().to_prompt_text(["atlas", "digest"])
    assert "## PIPELINE ATLAS" in text and "## SAMPLE DIGEST" in text
    assert "NF-CORE PIPELINE DOCS" not in text
    assert "NF-CORE SCHEMAS" not in text
    assert "fetched" not in text


def test_subset_order_follows_assembly_order_not_the_caller_s_order():
    text = _build().to_prompt_text(["schemas", "atlas"])
    assert text.index("## PIPELINE ATLAS") < text.index("## NF-CORE SCHEMAS")


def test_size_report_measures_only_the_chosen_sections():
    ctx = _build()
    full, subset = ctx.size_report(), ctx.size_report(["atlas", "digest"])
    assert subset["sections"] == ["atlas", "digest"]
    assert subset["docs"] == 0 and subset["schemas"] == 0
    assert subset["atlas"] == full["atlas"] and subset["digest"] == full["digest"]
    assert subset["total_chars"] < full["total_chars"]


def test_size_report_total_matches_the_text_it_describes():
    """A size report that does not measure the assembled string is a lie about
    cost — and cost is the whole point of the ablation this exists for."""
    ctx = _build()
    for sections in (None, ["atlas"], ["atlas", "digest"], ["digest", "schemas"],
                     ["atlas", "digest", "docs", "schemas"]):
        assert ctx.size_report(sections)["total_chars"] == len(ctx.to_prompt_text(sections))


def test_fetch_counts_are_zero_for_a_section_that_was_left_out():
    ctx = _build(schema_getter=_schema_getter(fail_for=("rnaseq",)))
    subset = ctx.size_report(["atlas", "digest"])
    assert subset["n_schemas_fetched"] == 0 and subset["n_schemas_failed"] == 0
    assert ctx.size_report()["n_schemas_failed"] == 1


def test_an_unknown_section_name_is_rejected_rather_than_ignored():
    with pytest.raises(ValueError, match="unknown payload section"):
        _build().to_prompt_text(["atlas", "digset"])


def test_sections_without_docs_fetches_no_docs():
    """Dropping the docs section must drop its fetches, not just its rendering."""
    doc_calls = []

    def docs_getter(pipeline, revision):
        doc_calls.append(pipeline)
        return {"readme": "r", "usage": "u", "output": "o"}

    ctx = build_selection_context(
        config=None, uids=["D.SEQ-1"], digest=DIGEST, atlas=ATLAS,
        schema_getter=_schema_getter(), docs_getter=docs_getter,
        sections=("atlas", "digest", "schemas"),
    )
    assert doc_calls == []
    assert ctx.docs == {}
    assert "NF-CORE PIPELINE DOCS" not in ctx.to_prompt_text(("atlas", "digest", "schemas"))


def test_sections_without_schemas_fetches_no_schemas():
    schema_calls = []

    def schema_getter(pipeline, revision):
        schema_calls.append(pipeline)
        return {"nextflow_schema": {}}

    build_selection_context(
        config=None, uids=["D.SEQ-1"], digest=DIGEST, atlas=ATLAS,
        schema_getter=schema_getter, docs_getter=lambda p, r: {},
        sections=("atlas", "digest"),
    )
    assert schema_calls == []


def test_sections_none_still_fetches_everything():
    """The default is unchanged, so the eval harness keeps its four-section arm."""
    doc_calls, schema_calls = [], []
    build_selection_context(
        config=None, uids=["D.SEQ-1"], digest=DIGEST, atlas=ATLAS,
        schema_getter=lambda p, r: schema_calls.append(p) or {"nextflow_schema": {}},
        docs_getter=lambda p, r: doc_calls.append(p) or {"readme": "r"},
    )
    assert sorted(doc_calls) == sorted(schema_calls)
    assert len(schema_calls) == len([k for k in RICH_PIPELINES if k in ATLAS["pipelines"]])


def test_size_ceiling_measures_only_the_requested_sections():
    """A payload under the ceiling for its own sections must not be refused
    because the sections it is not sending would have pushed it over."""
    big_digest = {**DIGEST, "filler": "x" * 40_000}
    ctx = build_selection_context(
        config=None, uids=["D.SEQ-1"], digest=big_digest, atlas=ATLAS,
        schema_getter=_schema_getter(), docs_getter=lambda p, r: {},
        sections=("atlas", "digest"), max_tokens=20_000,
    )
    assert ctx.size_report(("atlas", "digest"))["est_tokens"] <= 20_000
