import pytest

from NessieAI.ns import reingest_qa as qa
from NessieAI.ns.reingest import report

RUN = "nfcore_rnaseq_fixture"
ARTIFACTS = {"reingest_A.GEX": "/out/reingest_A.GEX.xlsx",
             "reingest_D.SEQ_update": "/out/reingest_D.SEQ_update.xlsx"}


def _soft_report(count=24):
    built = qa.QaReport()
    for i in range(count):
        built.add(qa.Finding(code=qa.UNAPPROVED_ATTRIBUTE, severity=qa.SOFT,
                             sample_type="D.SEQ", attribute="ContamPercent",
                             row_index=i,
                             detail={"raw_key": "Kraken2_bracken_fraction_total_reads",
                                     "example": "SAMPLE_01 = 3.2%", "times_proposed": 1}))
    return built._finalize()


def _single_finding_report(code, severity, **kwargs):
    built = qa.QaReport()
    built.add(qa.Finding(code=code, severity=severity, **kwargs))
    return built._finalize()


def test_n_rows_sharing_a_finding_render_as_one_sentence_not_n_lines():
    # A mutant that returns a fixed string regardless of input would make
    # `few` and `many` identical, so "24" could not be in one and not the
    # other, and the friendly name could not appear the same fixed number of
    # times for both -- this is the count-don't-enumerate rule made testable.
    few = report.render_qa_for_user({"D.SEQ": _soft_report(2)}, ARTIFACTS, RUN)
    many = report.render_qa_for_user({"D.SEQ": _soft_report(24)}, ARTIFACTS, RUN)

    assert few.lower().count("contamination percentage") == 1
    assert many.lower().count("contamination percentage") == 1
    assert "24" in many and "24" not in few
    assert "2" in few
    # The finding renders as one sentence, not N lines: growing the row count
    # from 2 to 24 must not grow the rendered text at all.
    assert len(many.splitlines()) == len(few.splitlines())


def test_the_raw_key_is_not_shown_to_the_user():
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    assert "Kraken2_bracken_fraction_total_reads" not in text
    # Pure-negative on its own: a mutant that returns a fixed string also
    # lacks the raw key. Pin the positive half too, so this test dies if the
    # measurement name it's supposed to show in its place ever goes missing.
    assert "contamination percentage" in text.lower()


def test_the_message_says_the_judgement_is_unconfirmed():
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    lowered = text.lower()
    assert "judgement" in lowered or "judgment" in lowered
    assert "confirm" in lowered


def test_the_unapproved_attribute_soft_flag_offers_upload_as_is_or_admin_confirmation():
    # Narrower than its name once claimed: this only checks the
    # UNAPPROVED_ATTRIBUTE/soft branch's two phrases. The general "every new
    # code offers its prescribed choice" claim is `_NEW_CODE_CASES` below.
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    assert "Leave it as-is" in text
    assert "administrator" in text


def test_a_clean_run_says_so_without_a_check_section():
    text = report.render_qa_for_user({"A.GEX": qa.QaReport()._finalize()}, ARTIFACTS, RUN)
    assert "TO CHECK" not in text.upper()
    # A vacuous negative isn't enough: the run must positively say it's ready.
    assert "ready" in text.lower()


def test_a_hard_reject_names_the_affected_samples_and_the_next_step():
    built = qa.QaReport()
    for i, sample in enumerate(["SAMPLE_22", "SAMPLE_23", "SAMPLE_24"]):
        built.add(qa.Finding(code=qa.UNRESOLVED_UID, severity=qa.HARD,
                             sample_type="D.SEQ", row_index=i,
                             detail={"nfcore_sample": sample,
                                     "fastq_1": f"/net/cluster/fastq/{sample}_R1.fastq.gz"}))
    text = report.render_qa_for_user({"D.SEQ": built._finalize()}, ARTIFACTS, RUN)
    assert "SAMPLE_22" in text and "SAMPLE_24" in text
    assert "blocked" in text.lower()


def test_the_upload_order_puts_children_before_the_backfill():
    reports = {"A.GEX": qa.QaReport()._finalize(), "D.SEQ": _soft_report()}

    text = report.render_qa_for_user(reports, ARTIFACTS, RUN)
    upload = text.split("TO UPLOAD", 1)[1]
    assert upload.index("reingest_A.GEX.xlsx") < upload.index("reingest_D.SEQ_update.xlsx")
    assert "update existing" in text.lower()

    # Prove the order tracks which artifact is actually the backfill, not
    # some property of the reports dict or a fixed sample-type name: swap
    # which sample type owns the "_update" workbook and the rendered order
    # must flip with it. A fixed-string mutant cannot contain both filename
    # orderings for two different artifact dicts.
    swapped_artifacts = {"reingest_A.GEX_update": "/out/reingest_A.GEX_update.xlsx",
                          "reingest_D.SEQ": "/out/reingest_D.SEQ.xlsx"}
    swapped_text = report.render_qa_for_user(reports, swapped_artifacts, RUN)
    swapped_upload = swapped_text.split("TO UPLOAD", 1)[1]
    assert swapped_upload.index("reingest_D.SEQ.xlsx") < swapped_upload.index("reingest_A.GEX_update.xlsx")
    assert "update existing" in swapped_text.lower()


def test_a_blocked_workbook_is_not_offered_for_upload():
    # Important 2: a HARD_REJECT sample type's workbook must not be listed
    # under TO UPLOAD -- it was just called blocked above.
    built = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="D.SEQ")
    text = report.render_qa_for_user({"D.SEQ": built}, ARTIFACTS, RUN)

    upload_section = text.split("TO UPLOAD", 1)[1]
    assert "reingest_D.SEQ_update.xlsx" not in upload_section
    assert "xlsx" not in upload_section
    assert "fix" in upload_section.lower() or "nothing" in upload_section.lower()


def test_a_blocked_child_holds_the_backfill_instead_of_an_unqualified_offer():
    # The hazardous direction: a HARD_REJECT child (A.GEX, a workbook that
    # creates new samples, not a backfill) must not leave an unrelated
    # backfill (D.SEQ_update) offered as a plain "go ahead" step -- its rows
    # would describe analysis samples that were never created. Compare a
    # clean child against a blocked one: the backfill's own report is
    # untouched (still SOFT_FLAG) in both runs, but its offered line must
    # change -- a constant-string mutant, or one that only suppresses the
    # blocked type's own workbook, cannot make this pair differ correctly.
    soft_backfill = _soft_report(3)
    artifacts = {"reingest_A.GEX": "/out/reingest_A.GEX.xlsx",
                 "reingest_D.SEQ_update": "/out/reingest_D.SEQ_update.xlsx"}

    clean_child = qa.QaReport()._finalize()
    clean_text = report.render_qa_for_user(
        {"A.GEX": clean_child, "D.SEQ": soft_backfill}, artifacts, RUN)
    clean_upload = clean_text.split("TO UPLOAD", 1)[1]
    assert "reingest_A.GEX.xlsx" in clean_upload
    assert 'tick "update existing samples"' in clean_upload

    hard_child = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="A.GEX")
    blocked_text = report.render_qa_for_user(
        {"A.GEX": hard_child, "D.SEQ": soft_backfill}, artifacts, RUN)
    blocked_upload = blocked_text.split("TO UPLOAD", 1)[1]

    # The blocked child's own workbook is never offered (Important 2).
    assert "reingest_A.GEX.xlsx" not in blocked_upload
    # The backfill is still named -- the reader must not lose track of it --
    # but it is no longer offered as an unqualified upload step.
    assert "reingest_D.SEQ_update.xlsx" in blocked_upload
    assert 'tick "update existing samples"' not in blocked_upload
    assert "hold until the blocked workbooks above are fixed" in blocked_upload

    assert clean_upload != blocked_upload


def test_severity_split_blockers_and_advisories_get_separate_headers():
    # Important 3: a hard finding must render under a blocking header with no
    # "leave it as-is" choice nearby, and a soft finding must render under the
    # checking header, where "Leave it as-is" is a real option.
    hard = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="Z.BOGUS")
    soft = _soft_report(3)
    text = report.render_qa_for_user({"Z.BOGUS": hard, "D.SEQ": soft}, ARTIFACTS, RUN)

    assert "WHAT IS BLOCKING" in text
    assert "CHECK BEFORE UPLOADING" in text
    blocking_at = text.index("WHAT IS BLOCKING")
    checking_at = text.index("CHECK BEFORE UPLOADING", blocking_at + len("WHAT IS BLOCKING"))
    leave_as_is_at = text.index("Leave it as-is")

    assert blocking_at < checking_at < leave_as_is_at
    # Each numbered item names which workbook it concerns.
    assert "Z.BOGUS" in text[blocking_at:checking_at]
    assert "D.SEQ" in text[checking_at:leave_as_is_at]


def test_lineage_unresolved_stands_on_its_own_again():
    # Restores the pre-e050ede1 behaviour. `Parent` no longer carries a
    # standing HARD exemption from the SEEK-flag split (see reingest_qa.py's
    # CATALOG_REQUIRED_MISSING comment for why that carve-out briefly
    # existed and why it was removed once the resolver could see A.*
    # parents): a row with no parent-ish key at all now produces only SOFT
    # findings on "Parent" -- the catalog-only CATALOG_REQUIRED_MISSING
    # (SEEK's own required flag is 0 on it) alongside the three-state
    # LINEAGE_UNRESOLVED. With nothing HARD in the picture,
    # LINEAGE_UNRESOLVED's own "ships anyway, upload as-is" story is true
    # again and must render without deferring to anything.
    built = qa.QaReport()
    built.add(qa.Finding(code=qa.CATALOG_REQUIRED_MISSING, severity=qa.SOFT,
                         sample_type="A.GEX", attribute="Parent", row_index=0))
    built.add(qa.Finding(code=qa.LINEAGE_UNRESOLVED, severity=qa.SOFT,
                         sample_type="A.GEX", row_index=0,
                         detail={"reason": "no Parent key present (lineage could not be resolved)"}))
    text = report.render_qa_for_user({"A.GEX": built._finalize()}, ARTIFACTS, RUN)

    assert "Reingest ready" in text
    assert "WHAT IS BLOCKING" not in text
    assert "no parent found" in text.lower()
    assert "upload fine" in text.lower()
    # The same missing Parent is not reported a second time as a missing field.
    assert "parent missing" not in text.lower()
    # The workbook IS offered for upload: nothing here is HARD any more.
    upload_section = text.split("TO UPLOAD", 1)[1]
    assert "reingest_A.GEX.xlsx" in upload_section


_NEW_CODE_CASES = [
    (qa.UNKNOWN_SAMPLETYPE, qa.HARD,
     dict(sample_type="Z.BOGUS"),
     ["catalog", "administrator"]),
    (qa.BLANK_PARENT, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"reason": "blank Parent"}),
     ["input sample", "which sequencing sample"]),
    (qa.LINEAGE_UNRESOLVED, qa.SOFT,
     dict(sample_type="A.GEX", row_index=0,
          detail={"reason": "no Parent key present (lineage could not be resolved)"}),
     ["no parent found", "attach the parents", "rebuild"]),
    (qa.PARENT_UID_NOT_FOUND, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"token": "D.SEQ-EXAMPLE-1"}),
     ["does not resolve", "register"]),
    (qa.DUPLICATE_NAME, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"name": "SAMPLE_X"}),
     ["already used", "rename"]),
    (qa.SURPRISE_SENTINEL, qa.SOFT,
     dict(sample_type="D.SEQ", row_index=0, attribute="Notes",
          detail={"sentinel": "TODO"}),
     ["placeholder", "confirm"]),
    (qa.UID_MISSING_IN_UPDATE, qa.HARD,
     dict(sample_type="D.SEQ", attribute="UID", row_index=0),
     ["existing sample", "identify"]),
    (qa.UID_PRESENT_IN_NEW, qa.HARD,
     dict(sample_type="D.SEQ", attribute="UID", row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1"}),
     # "carries" (not the malformed "carrys" _verb's fallback would produce
     # for a count of exactly one, which _single_finding_report always is)
     # pins the singular subject-verb agreement fixed alongside this case.
     ["cannot be mixed", "which was intended", "carries"]),
    (qa.UNAPPROVED_ATTRIBUTE, qa.SOFT,
     dict(sample_type="D.SEQ", attribute="MappedPercent", row_index=0,
          detail={"example": "SAMPLE_01 = 91%"}),
     ["sanity-check", "leave it as-is", "administrator"]),
    (qa.ATTRIBUTE_NOT_DEFINED, qa.SOFT,
     dict(sample_type="D.SEQ", attribute="DuplicationPercent", row_index=0),
     ["nowhere to live", "administrators", "re-run"]),
    (qa.MULTIRUN_NOT_ATTRIBUTABLE, qa.SOFT,
     dict(sample_type="D.SEQ", row_index=0),
     ["merged", "left out of the backfill"]),
    (qa.MISSING_REQUIRED, qa.HARD,
     dict(sample_type="D.SEQ", attribute="Strandedness", row_index=0),
     ["required and missing", "fill it in"]),
    (qa.CATALOG_REQUIRED_MISSING, qa.SOFT,
     dict(sample_type="A.GEX", attribute="Checksum_PrimaryData", row_index=0),
     ["no checksum", "accepts these rows", "upload as-is"]),
    (qa.PRIMARY_DATA_LINK_ONLY, qa.SOFT,
     dict(sample_type="A.GEX",
          attribute=qa.group_label(("File_PrimaryData", "Link_PrimaryData")),
          row_index=0,
          detail={"primary": "File_PrimaryData", "present_secondary": "Link_PrimaryData"}),
     ["may still require", "let the", "settle whether"]),
    (qa.PRIMARY_DATA_UNNAMED, qa.HARD,
     dict(sample_type="A.GEX",
          attribute=qa.group_label(("File_PrimaryData", "Link_PrimaryData")),
          row_index=0,
          detail={"primary": "File_PrimaryData", "present_secondary": "Link_PrimaryData"}),
     ["cannot title the sample", "neither", "tell me where to get one"]),
    ("some_future_code_without_wording_yet", qa.SOFT,
     dict(sample_type="D.SEQ"),
     ["needs a look", "provenance"]),
]


def test_each_new_code_offers_the_prescribed_choice_and_leaks_no_raw_code():
    for code, severity, kwargs, expected_phrases in _NEW_CODE_CASES:
        built = _single_finding_report(code, severity, **kwargs)
        text = report.render_qa_for_user({kwargs["sample_type"]: built}, ARTIFACTS, RUN)
        # Collapse wrapped lines so a phrase split across the prose's own
        # line breaks still matches a contiguous check.
        lowered = " ".join(text.lower().split())
        for phrase in expected_phrases:
            assert phrase.lower() in lowered, f"{code}: missing {phrase!r} in:\n{text}"
        assert code not in text, f"{code}: leaked its own developer code:\n{text}"


def test_notes_would_clobber_distinguishes_not_fetched_from_would_overwrite():
    not_fetched = _single_finding_report(
        qa.NOTES_WOULD_CLOBBER, qa.HARD, sample_type="D.SEQ", attribute="Notes",
        row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1",
                              "reason": "existing Notes not fetched"})
    text = report.render_qa_for_user({"D.SEQ": not_fetched}, ARTIFACTS, RUN)
    lowered = text.lower()
    assert "could not read" in lowered
    assert "nothing was written" in lowered
    assert qa.NOTES_WOULD_CLOBBER not in text

    would_overwrite = _single_finding_report(
        qa.NOTES_WOULD_CLOBBER, qa.HARD, sample_type="D.SEQ", attribute="Notes",
        row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1",
                              "reason": "existing text absent"})
    text2 = report.render_qa_for_user({"D.SEQ": would_overwrite}, ARTIFACTS, RUN)
    lowered2 = text2.lower()
    assert "does not contain" in lowered2
    assert "nothing was written" in lowered2
    assert qa.NOTES_WOULD_CLOBBER not in text2

    # The two reasons must not read identically.
    assert text != text2


def test_missing_required_names_both_alternatives_when_the_attribute_is_a_group():
    # NessieAI/ns/reingest_qa.ALTERNATIVE_REQUIRED_GROUPS: an absent
    # File_PrimaryData/Link_PrimaryData pair renders as one finding whose
    # attribute is the group label -- the reader must be told every
    # alternative attribute, not just shown one bare name. This is the
    # neither-present case, so it is HARD (see the SOFT case below for a
    # secondary supplied alone).
    group_label = qa.group_label(("File_PrimaryData", "Link_PrimaryData"))
    built = _single_finding_report(
        qa.MISSING_REQUIRED, qa.HARD, sample_type="A.GEX",
        attribute=group_label, row_index=0)
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "File_PrimaryData" in text
    assert "Link_PrimaryData" in text
    # The concrete phrase naming both alternatives together, not just the
    # bare word "or" -- "or" alone would also match "before", "for", or the
    # word "or" occurring anywhere by coincidence in surrounding prose, so it
    # can never fail even if the group label were dropped entirely.
    assert "File_PrimaryData or Link_PrimaryData" in text
    assert qa.MISSING_REQUIRED not in text

    # A plain (ungrouped) MISSING_REQUIRED must not gain either alternative's
    # name: the group-aware wording is only for an actual group label.
    plain = _single_finding_report(
        qa.MISSING_REQUIRED, qa.HARD, sample_type="A.GEX",
        attribute="Checksum_PrimaryData", row_index=0)
    plain_text = report.render_qa_for_user({"A.GEX": plain}, ARTIFACTS, RUN)
    assert "File_PrimaryData" not in plain_text
    assert "Link_PrimaryData" not in plain_text
    # The reworded prose must still be true: it must not claim a server
    # rejection that startup/seed/seek_production.sql.gz's required=0 rows
    # (e.g. Checksum_PrimaryData on A.GEX/A.ALN/A.SCXP/D.SEQ) contradict.
    assert "server will reject" not in plain_text.lower()


def test_a_missing_primary_file_says_the_map_rule_matched_nothing():
    # The mapper looked for the file and found none, and a data file is never
    # set from chat (answers.check_fill), so asking the reader to "fill in" the
    # path sends them round a loop the server will refuse.
    group_label = qa.group_label(("File_PrimaryData", "Link_PrimaryData"))
    built = _single_finding_report(
        qa.MISSING_REQUIRED, qa.HARD, sample_type="A.GEX",
        attribute=group_label, row_index=0)
    text = report.render_qa_for_user(
        {"A.GEX": built}, ARTIFACTS, RUN,
        missed_rules=[{"rule_id": "counts", "sample_type": "A.GEX"}])
    assert 'rule "counts"' in text
    assert "can't be filled in from chat" in text
    assert "Fill in" not in text

    # A miss on another sample type leaves this one's wording alone.
    other = report.render_qa_for_user(
        {"A.GEX": built}, ARTIFACTS, RUN,
        missed_rules=[{"rule_id": "aln", "sample_type": "A.ALN"}])
    assert other == report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)


def test_missing_required_group_rendering_tolerates_a_single_member_group(monkeypatch):
    # Minor 3: the rewritten is_group_label branch does
    # `" or ".join(_name(m) for m in members[1:])`, which for a one-member
    # group renders "a  value may be accepted too" (a blank secondary) -- the
    # code it replaced carried an explicit comment that nothing there assumed
    # exactly two. Today's one real group (File_PrimaryData/Link_PrimaryData)
    # always has 2+ members, so this simulates a hypothetical single-member
    # group by monkeypatching is_group_label/group_members_for_label, and
    # pins that the branch falls back to the plain wording instead of
    # crashing or rendering a blank alternative.
    fake_label = "SoloRequired"
    monkeypatch.setattr(qa, "is_group_label", lambda attribute: attribute == fake_label)
    monkeypatch.setattr(qa, "group_members_for_label",
                        lambda attribute: ("SoloRequired",) if attribute == fake_label else None)
    built = _single_finding_report(
        qa.MISSING_REQUIRED, qa.HARD, sample_type="A.GEX",
        attribute=fake_label, row_index=0)
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "may be accepted too" not in text
    assert "SoloRequired is required and missing" in text
    assert qa.MISSING_REQUIRED not in text


def test_primary_data_link_only_soft_flags_when_only_a_secondary_is_present():
    # reingest_qa.qa_rows only emits PRIMARY_DATA_LINK_ONLY when
    # File_PrimaryData (the primary) is absent but Link_PrimaryData (a
    # secondary) is present *and* the row can still be named -- the safe
    # direction (primary alone) never produces a finding at all,
    # neither-present is the MISSING_REQUIRED HARD case above, and a Link-only
    # row with no Name either is the separate PRIMARY_DATA_UNNAMED HARD case
    # (Important 3). This must read differently from both HARD cases: it
    # names which attribute was supplied and which is still missing (by their
    # friendly names -- Minor 4 gave File_PrimaryData/Link_PrimaryData
    # _FRIENDLY entries), and it must not claim the server will reject the
    # row -- it might not.
    group_label = qa.group_label(("File_PrimaryData", "Link_PrimaryData"))
    built = _single_finding_report(
        qa.PRIMARY_DATA_LINK_ONLY, qa.SOFT, sample_type="A.GEX",
        attribute=group_label, row_index=0,
        detail={"primary": "File_PrimaryData",
                "present_secondary": "Link_PrimaryData"})
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    lowered = text.lower()
    assert "the download link" in lowered
    assert "the file path" in lowered
    assert "may still require" in lowered
    assert "server will reject" not in lowered
    assert qa.PRIMARY_DATA_LINK_ONLY not in text
    # It renders under the advisory header, not the blocking one.
    assert "CHECK BEFORE UPLOADING" in text
    assert "WHAT IS BLOCKING" not in text


def test_mixed_hard_and_soft_primary_data_findings_in_one_batch_both_render():
    # Important 1 regression: before PRIMARY_DATA_LINK_ONLY/PRIMARY_DATA_UNNAMED
    # got their own codes, both the SOFT (link-only, named) and HARD
    # (neither present) cases shared code=MISSING_REQUIRED with the same
    # group-label attribute. qa.group() keys on (code, attribute) and takes
    # severity from the first finding seen, so a mixed batch collapsed into
    # ONE bucket whose severity depended on row order -- the other severity's
    # findings, and their count, vanished from the render entirely. A row
    # order of 3 SOFT-eligible rows (link only, but named) then 7 HARD rows
    # (neither present) reproduces the exact failure scenario from the
    # report: with the old shared code this became one SOFT bucket with
    # count 10, "WHAT IS BLOCKING" would render nothing despite
    # disposition HARD_REJECT. With separate codes both buckets survive,
    # each under its own header, with its own correct count (3 and 7, not
    # 10 and 10).
    group_label = qa.group_label(("File_PrimaryData", "Link_PrimaryData"))
    built = qa.QaReport()
    for i in range(3):
        built.add(qa.Finding(code=qa.PRIMARY_DATA_LINK_ONLY, severity=qa.SOFT,
                             sample_type="A.GEX", attribute=group_label, row_index=i,
                             detail={"primary": "File_PrimaryData",
                                     "present_secondary": "Link_PrimaryData"}))
    for i in range(3, 10):
        built.add(qa.Finding(code=qa.MISSING_REQUIRED, severity=qa.HARD,
                             sample_type="A.GEX", attribute=group_label, row_index=i))
    built._finalize()
    assert built.disposition == qa.HARD_REJECT

    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)

    assert "WHAT IS BLOCKING" in text
    assert "CHECK BEFORE UPLOADING" in text
    blocking_at = text.index("WHAT IS BLOCKING")
    checking_at = text.index("CHECK BEFORE UPLOADING", blocking_at + len("WHAT IS BLOCKING"))
    hard_section = text[blocking_at:checking_at]
    soft_section = text[checking_at:]

    # Both findings render, each with its own correct count. Scoped to the
    # actual counted phrase ("N row(s) in") rather than a bare digit scan --
    # Minor 5: a bare "7"/"10" can also match a footer, a fixture name, or an
    # unrelated number, so it would break on an unrelated change elsewhere in
    # the render.
    assert "7 rows in" in hard_section
    assert "3 rows in" in soft_section
    # Neither count leaks into the other section, and the two are never
    # merged back into the total of 10.
    assert "7 rows in" not in soft_section
    assert "10 rows in" not in text


def test_resolve_artifact_matches_a_hyphenated_sample_type():
    # Regression: granular.py's safe_key replaces ".", "-", "/" and space with
    # "_" (see its comment beside `safe_key`); _resolve_artifact must
    # normalise identically or a sample type like "A.MADE-UP-TYPE" (this
    # branch's own fixture, test_build_upload_xlsx_op.py) resolves to
    # "reingest_A_MADE-UP-TYPE" here while granular.py actually saved
    # "reingest_A_MADE_UP_TYPE", and the workbook silently disappears from
    # both the status list and TO UPLOAD.
    artifacts = {"reingest_A_MADE_UP_TYPE": "/out/reingest_A.MADE-UP-TYPE.xlsx"}
    key, path = report._resolve_artifact(artifacts, "A.MADE-UP-TYPE")
    assert (key, path) == ("reingest_A_MADE_UP_TYPE", "/out/reingest_A.MADE-UP-TYPE.xlsx")

    clean = qa.QaReport()._finalize()
    text = report.render_qa_for_user({"A.MADE-UP-TYPE": clean}, artifacts, RUN)
    upload_section = text.split("TO UPLOAD", 1)[1]
    assert "reingest_A.MADE-UP-TYPE.xlsx" in upload_section


def test_unresolved_uid_caps_the_sample_list_instead_of_enumerating_all():
    built = qa.QaReport()
    samples = [f"SAMPLE_{i:03d}" for i in range(40)]
    for i, sample in enumerate(samples):
        built.add(qa.Finding(code=qa.UNRESOLVED_UID, severity=qa.HARD,
                             sample_type="D.SEQ", row_index=i,
                             detail={"nfcore_sample": sample}))
    text = report.render_qa_for_user({"D.SEQ": built._finalize()}, ARTIFACTS, RUN)
    assert "40" in text
    assert "and 30 more" in text
    # Not all 40 sample names are dumped into the chat.
    assert text.count("SAMPLE_0") + text.count("SAMPLE_1") + text.count("SAMPLE_2") + \
        text.count("SAMPLE_3") < 40


def test_ambiguous_primary_data_reaches_the_reply_verbatim():
    # Minor 1 (2026-09-17 review): a silent File_PrimaryData pick among
    # same-basename candidates (see mapper.py's `_attach_checksum`) must
    # reach the reply the agent relays verbatim, naming both candidates and
    # the one used, not just get logged somewhere a curator won't see.
    clean = qa.QaReport()._finalize()
    ambiguous_primary = [{
        "sample_type": "A.GEX", "attribute": "File_PrimaryData",
        "chosen": "salmon/all.merged.gene_counts.tsv",
        "candidates": ["salmon/all.merged.gene_counts.tsv",
                       "star_salmon/all.merged.gene_counts.tsv"],
    }]
    text = report.render_qa_for_user({"A.GEX": clean}, ARTIFACTS, RUN,
                                     ambiguous_primary=ambiguous_primary)
    assert "PRIMARY-FILE PICK" in text
    assert "salmon/all.merged.gene_counts.tsv" in text
    assert "star_salmon/all.merged.gene_counts.tsv" in text
    # Reworded in the module's own voice (Minor 5, 2026-09-17 review): no
    # bare "rule" -- a map-internals concept -- and it points at the real
    # remedy (run-checksum + rebuild), not the unimplemented "tell me which
    # one" the agent had no way to act on.
    assert "A.GEX's primary data" in text
    assert "rule" not in text.lower()
    assert "run the checksum step" in text
    # The section lands before the upload steps, not after.
    assert text.index("PRIMARY-FILE PICK") < text.index("TO UPLOAD")


def test_no_ambiguous_primary_data_omits_the_section():
    clean = qa.QaReport()._finalize()
    text = report.render_qa_for_user({"A.GEX": clean}, ARTIFACTS, RUN)
    assert "PRIMARY-FILE" not in text


# --- One item per finding, across workbooks ---------------------------------
# A finding shared by several workbooks is one item naming each of them, so the
# reader sees at once which workbooks are affected; it used to be one numbered
# item per workbook, and a missing Parent was reported twice (no parent
# identified, and Parent missing) for the same rows.

_TWO_WORKBOOKS = {"reingest_A_ALN": "/out/reingest_A.ALN.xlsx",
                  "reingest_A_GEX": "/out/reingest_A.GEX.xlsx"}


def _unparented(sample_type, n):
    built = qa.QaReport()
    for i in range(n):
        built.add(qa.Finding(code=qa.LINEAGE_UNRESOLVED, severity=qa.SOFT,
                             sample_type=sample_type, row_index=i,
                             detail={"reason": "no Parent key present"}))
        built.add(qa.Finding(code=qa.CATALOG_REQUIRED_MISSING, severity=qa.SOFT,
                             sample_type=sample_type, attribute="Parent", row_index=i))
        built.add(qa.Finding(code=qa.CATALOG_REQUIRED_MISSING, severity=qa.SOFT,
                             sample_type=sample_type, attribute="Checksum_PrimaryData",
                             row_index=i))
    return built._finalize()


def _two_workbook_text(**kwargs):
    reports = {"A.ALN": _unparented("A.ALN", 15), "A.GEX": _unparented("A.GEX", 1)}
    return report.render_qa_for_user(reports, _TWO_WORKBOOKS, RUN, **kwargs)


def test_a_finding_shared_by_two_workbooks_is_one_item_naming_each():
    text = _two_workbook_text()
    parent_lines = [l for l in text.splitlines() if "No parent found" in l]
    assert len(parent_lines) == 1
    assert "reingest_A.ALN.xlsx (15 rows)" in parent_lines[0]
    assert "reingest_A.GEX.xlsx (1 row)" in parent_lines[0]
    checksum_lines = [l for l in text.splitlines() if "No checksum" in l]
    assert len(checksum_lines) == 1


def test_parent_missing_is_not_repeated_where_no_parent_was_found():
    text = _two_workbook_text()
    assert "Parent missing" not in text
    # Two findings, two items: no parent, no checksum.
    check = text.split("CHECK BEFORE UPLOADING", 1)[1].split("TO UPLOAD", 1)[0]
    assert [l.split(".", 1)[0].strip() for l in check.splitlines()
            if l.strip()[:2] in ("1.", "2.", "3.")] == ["1", "2"]


def test_each_workbook_is_listed_with_its_row_count():
    text = _two_workbook_text(row_counts={"A.ALN": 15, "A.GEX": 1})
    head = text.split("CHECK BEFORE UPLOADING", 1)[0]
    assert "reingest_A.ALN.xlsx — 15 rows" in head
    assert "reingest_A.GEX.xlsx — 1 row" in head
    assert "!" not in head


def test_unmapped_metrics_are_one_line():
    text = _two_workbook_text(unmapped_count=24)
    lines = [l for l in text.splitlines() if "24 QC metrics" in l]
    assert len(lines) == 1
    assert "review" in lines[0]
    assert "QC metrics" not in _two_workbook_text()


@pytest.mark.parametrize("code, severity, detail, expect", [
    (qa.ASSAY_TO_CREATE, qa.SOFT,
     {"study_id": 51, "study_title": "Kamm", "assay_title": "Genome Alignment - Data Linked"},
     "The upload will create 'Genome Alignment - Data Linked' in study 51 (Kamm)"),
    (qa.ASSAY_AMBIGUOUS, qa.HARD,
     {"study_id": 17, "study_title": "S", "assay_ids": [40, 41]},
     "study 17 (S) has more than one assay of this kind (40, 41)"),
    (qa.ASSAY_AMBIGUOUS, qa.HARD,
     {"study_id": 17, "study_title": "S", "assay_ids": [40, 41]},
     "Remove the extra assay in SEEK, then rebuild."),
    (qa.HOUSE_ASSAY_MISSING, qa.HARD, {"assay": "Variant Call Filtering"},
     "there is no house assay 'Variant Call Filtering' on this instance"),
    (qa.HOUSE_ASSAY_MISSING, qa.HARD, {"assay": "Variant Call Filtering"},
     "An administrator needs to add this assay type to NExtSEEK; then rebuild."),
    (qa.PARENT_IN_NO_STUDY, qa.HARD, {"parents": ["D.SEQ-1"]},
     "not in any study's assay: D.SEQ-1"),
    (qa.ASSAY_NOT_DECLARED, qa.HARD, {},
     "NExtSEEK does not yet say which assay these results belong to."),
    (qa.ASSAY_NOT_DECLARED, qa.HARD, {}, "An administrator needs to set that up; then rebuild."),
    (qa.ASSAY_SPLIT_ACROSS_STUDIES, qa.HARD, {}, "come from different studies"),
    (qa.PARENTS_JOIN_ASSAY, qa.SOFT, {"parents": 2, "assays": ["Gene Expression Analysis"]},
     "also add the 2 parent samples to Gene Expression Analysis"),
    (qa.PARENTS_JOIN_ASSAY, qa.SOFT, {"parents": 2, "assays": ["Gene Expression Analysis"]},
     "so NExtSEEK shows which samples each new one came from in that assay."),
])
def test_assay_findings_say_what_happens(code, severity, detail, expect):
    built = _single_finding_report(code, severity, sample_type="A.GEX", row_index=0, detail=detail)
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert expect in text
    assert code not in text


def test_an_unresolved_test_only_assay_is_one_line():
    built = _single_finding_report(qa.ASSAY_UNRESOLVED_TEST_ONLY, qa.SOFT, sample_type="A.GEX",
                                   row_index=0, detail={"assay": "Gene Expression Analysis"})
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "these rows have no parent samples, so no study assay could be found" in text
    assert "'Gene Expression Analysis'" in text
    assert "This build is for testing only." in text


def test_one_parent_sample_is_not_pluralised():
    built = _single_finding_report(qa.PARENTS_JOIN_ASSAY, qa.SOFT, sample_type="A.GEX",
                                   row_index=0, detail={"parents": 1, "assays": ["Gene Expression"]})
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "the 1 parent sample to Gene Expression" in text
    assert "1 parent samples" not in text


def _multi_report(code, severity, details):
    built = qa.QaReport()
    for i, detail in enumerate(details):
        built.add(qa.Finding(code=code, severity=severity, sample_type="A.GEX",
                             row_index=i, detail=detail))
    return built._finalize()


def test_parent_in_no_study_lists_the_parents_of_every_row():
    built = _multi_report(qa.PARENT_IN_NO_STUDY, qa.HARD,
                          [{"parents": ["D.SEQ-1"]}, {"parents": ["D.SEQ-2", "D.SEQ-1"]}])
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "2 rows" in text
    assert "D.SEQ-1, D.SEQ-2" in text
    assert text.count("D.SEQ-1") == 1


def test_parent_in_no_study_caps_the_parent_list():
    built = _multi_report(qa.PARENT_IN_NO_STUDY, qa.HARD,
                          [{"parents": [f"D.SEQ-{i:03d}"]} for i in range(25)])
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "25 rows" in text
    assert "and 15 more" in text
    assert "D.SEQ-000" in text
    assert "D.SEQ-024" not in text


def test_assay_to_create_gets_one_line_per_study_with_its_own_row_count():
    built = qa.QaReport()
    for row, (study, title) in enumerate([(51, "Kamm"), (51, "Kamm"), (51, "Kamm"), (52, "Vo")]):
        built.add(qa.Finding(code=qa.ASSAY_TO_CREATE, severity=qa.SOFT, sample_type="A.GEX",
                             row_index=row,
                             detail={"study_id": study, "study_title": title,
                                     "assay_title": "Genome Alignment - Data Linked"}))
    # Two findings on one row (a row that sits in both studies) is still one row.
    built.add(qa.Finding(code=qa.ASSAY_TO_CREATE, severity=qa.SOFT, sample_type="A.GEX",
                         row_index=3,
                         detail={"study_id": 51, "study_title": "Kamm",
                                 "assay_title": "Genome Alignment - Data Linked"}))
    text = report.render_qa_for_user({"A.GEX": built._finalize()}, ARTIFACTS, RUN)
    assert ("create 'Genome Alignment - Data Linked' in study 51 (Kamm)"
            " and file 4 rows of the A.GEX workbook under it") in text
    assert ("create 'Genome Alignment - Data Linked' in study 52 (Vo)"
            " and file 1 row of the A.GEX workbook under it") in text


def test_assay_ambiguous_gets_one_line_per_study_with_its_assays_and_rows():
    built = _multi_report(qa.ASSAY_AMBIGUOUS, qa.HARD, [
        {"study_id": 17, "study_title": "S", "assay_ids": [40, 41]},
        {"study_id": 17, "study_title": "S", "assay_ids": [40, 41]},
        {"study_id": 18, "study_title": "T", "assay_ids": [50, 51]}])
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "2 rows in the A.GEX workbook: study 17 (S) has more than one assay of this kind (40, 41)" in text
    assert "1 row in the A.GEX workbook: study 18 (T) has more than one assay of this kind (50, 51)" in text
    assert text.count("Remove the extra assay in SEEK, then rebuild.") == 1


def test_a_real_row_with_no_parents_blocks_and_says_what_to_check():
    built = _multi_report(qa.ASSAY_NO_PARENTS, qa.HARD, [{"assay": "Genome Alignment"}] * 3)
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "WHAT IS BLOCKING" in text
    assert ("the A.GEX workbook: these rows have no parent samples, so they cannot be filed "
            "under a study. Check the run's sample sheet, then rebuild.") in text
    assert text.count("these rows have no parent samples") == 1
    assert qa.ASSAY_NO_PARENTS not in text


def test_two_house_assays_for_one_name_block_in_plain_words():
    built = _multi_report(qa.HOUSE_ASSAY_AMBIGUOUS, qa.HARD, [{"assay": "Genome Alignment"}] * 2)
    text = report.render_qa_for_user({"A.GEX": built}, ARTIFACTS, RUN)
    assert "WHAT IS BLOCKING" in text
    assert ("the A.GEX workbook: NExtSEEK has more than one assay type that matches "
            "'Genome Alignment'; an administrator needs to resolve that, then rebuild.") in text
    assert qa.HOUSE_ASSAY_AMBIGUOUS not in text
