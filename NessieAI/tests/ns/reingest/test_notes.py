from NessieAI.ns.reingest import notes

RUN = "nfcore_rnaseq_fixture"
VALUES = {"Strandedness": "reverse", "DuplicationPercent": 18.4}


def test_existing_text_is_preserved_above_the_block():
    out = notes.compose("Resequenced after low yield.", RUN, VALUES, "2026-09-15")
    assert out.startswith("Resequenced after low yield.")
    assert f"[nfcore-reingest 2026-09-15 {RUN}]" in out
    assert "Strandedness=reverse" in out


def test_an_empty_existing_note_yields_the_block_alone():
    out = notes.compose("", RUN, VALUES, "2026-09-15")
    assert out.startswith("[nfcore-reingest")


def test_recomposing_replaces_this_runs_block_rather_than_appending_twice():
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    twice = notes.compose(once, RUN, VALUES, "2026-09-16")
    assert twice.count("[nfcore-reingest") == 1
    assert twice.startswith("Keep me.")
    assert "2026-09-16" in twice


def test_a_block_from_a_different_run_is_left_alone():
    other = notes.compose("Keep me.", "other_run", {"X": 1}, "2026-09-01")
    both = notes.compose(other, RUN, VALUES, "2026-09-15")
    assert "other_run" in both
    assert both.count("[nfcore-reingest") == 2


def test_prose_below_a_blank_line_survives_a_recompose():
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    with_prose = f"{once}\n\nCurator added later."
    twice = notes.compose(with_prose, RUN, VALUES, "2026-09-16")
    assert "Curator added later." in twice
    assert twice.count("[nfcore-reingest") == 1
    assert "2026-09-16" in twice


def test_prose_written_with_no_blank_line_above_it_now_survives_via_the_terminator():
    # Regression for the Critical: the block's last line is a real
    # terminator, not an ordinary body line, so a curator line typed
    # immediately under it -- no blank line, one Enter keypress short of the
    # old "safe" form -- is no longer indistinguishable from block content.
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    no_blank_line = f"{once}\nCurator added later."
    twice = notes.compose(no_blank_line, RUN, VALUES, "2026-09-16")
    assert "Curator added later." in twice
    assert twice.count("[nfcore-reingest") == 1
    assert "2026-09-16" in twice


def test_prose_below_the_terminator_survives_three_successive_recomposes():
    # Guards against slow accumulation: no extra tag or terminator lines pile
    # up across repeated runs, and the curator's line is never duplicated.
    run1 = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    text = f"{run1}\nCurator added later."
    for today in ("2026-09-16", "2026-09-17", "2026-09-18"):
        text = notes.compose(text, RUN, VALUES, today)
        assert text.count("Curator added later.") == 1
        assert text.count("[nfcore-reingest") == 1
        assert text.count(notes._TERMINATOR) == 1
    assert "2026-09-18" in text


def test_an_unterminated_tag_line_is_left_alone_by_strip_block():
    # No terminator anywhere for this run's tag can only mean the
    # terminator was typo'd, edited, deleted, or line-wrapped -- _block()
    # has emitted one on every block since its very first line, so there
    # is no such thing as genuinely pre-terminator data in this module.
    # strip_block cannot safely guess where such a block ends, so it must
    # not touch it at all: consuming nothing is the only safe answer.
    text = (f"Keep me.\n\n{notes._TAG} 2026-09-15 {RUN}]\n"
            "OldMetric=1.0\nCURATOR LINE NO BLANK.")
    assert notes.strip_block(text, RUN) == text


def test_a_recompose_over_an_unterminated_block_leaves_all_prose_intact():
    # Finding 1 repro. This exact shape used to lose "CURATOR LINE NO
    # BLANK." on recompose, silently, via the now-deleted blank-line
    # fallback (the fallback swallowed every non-empty line after the tag,
    # curator prose included, whenever no blank line separated them). With
    # strip_block refusing to touch an unterminated tag line at all,
    # compose's `kept` is the untouched original text in full: nothing a
    # curator wrote is ever a candidate for removal here.
    prior = (f"Keep me.\n\n{notes._TAG} 2026-09-15 {RUN}]\n"
             "OldMetric=1.0\nCURATOR LINE NO BLANK.")
    twice = notes.compose(prior, RUN, VALUES, "2026-09-16")
    assert "CURATOR LINE NO BLANK." in twice
    assert "OldMetric=1.0" in twice
    assert "2026-09-16" in twice


def test_the_guard_hard_rejects_a_notes_value_that_drops_what_strip_block_would_not_touch():
    # Because strip_block now licenses NOTHING to disappear from an
    # unterminated tag line (previous test), the guard's own
    # notes.strip_block(prior, run_name) call resolves to the WHOLE prior
    # text for this shape. Any actual Notes write that does not contain
    # that verbatim is a real drop -- not a licensed one -- and the guard
    # must refuse it instead of staying silent the way it did under the
    # deleted fallback (which licensed the exact same over-consumption on
    # both sides of the comparison, see the module docstring).
    from NessieAI.ns import reingest_qa as qa

    prior = (f"Keep me.\n\n{notes._TAG} 2026-09-15 {RUN}]\n"
             "OldMetric=1.0\nCURATOR LINE NO BLANK.")
    # A Notes value that kept "Keep me." but dropped the old block's own
    # content ("OldMetric=1.0" and the curator's line under it) -- exactly
    # what the deleted fallback used to produce, and exactly the shape the
    # guard used to wave through because it computed the same
    # over-consumed `prior_for_compare`.
    lossy_notes = f"Keep me.\n\n{notes._block(RUN, VALUES, '2026-09-16')}"
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "Notes": lossy_notes}}],
        sample_type="D.SEQ", known_sampletypes={"D.SEQ"}, mode="update",
        existing_notes={"D.SEQ-EXAMPLE-1": prior}, run_name=RUN)
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_content_edited_inside_the_block_is_licensed_to_vanish():
    # The block is machine-owned end to end: a value line between the tag
    # and the terminator is not curator prose, so tampering with it (by
    # hand, or by any other means) does not move the boundary -- the whole
    # block, tampered content included, is still replaced wholesale on the
    # next recompose. This pins the license's upper edge as deliberate,
    # not incidental.
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    tampered = once.replace("Strandedness=reverse", "Strandedness=CURATOR EDITED THIS")
    twice = notes.compose(tampered, RUN, VALUES, "2026-09-16")
    assert "CURATOR EDITED THIS" not in twice
    assert twice.count("[nfcore-reingest") == 1
    assert "2026-09-16" in twice


def test_a_curator_line_that_merely_resembles_the_terminator_is_ordinary_prose():
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    lookalike = "Anything you jot down here stays put, no need to worry."
    with_prose = f"{once}\n{lookalike}"
    twice = notes.compose(with_prose, RUN, VALUES, "2026-09-16")
    assert lookalike in twice
    assert twice.count("[nfcore-reingest") == 1


class TestStripBlock:
    """Direct tests of strip_block's own boundary handling."""

    def test_a_run_name_that_is_a_prefix_of_another_run_name_does_not_collide(self):
        # "run" must not match a block tagged "run_2" -- the "]" terminator
        # right after the run name is what prevents that.
        text = notes.compose("", "run_2", {"X": 1}, "2026-09-01")
        stripped = notes.strip_block(text, "run")
        assert stripped == text
        assert "run_2" in stripped

    def test_a_block_that_is_the_only_content_strips_to_empty(self):
        text = notes.compose("", RUN, VALUES, "2026-09-15")
        assert notes.strip_block(text, RUN) == ""

    def test_an_unterminated_leftover_for_this_run_survives_untouched_and_does_not_grow(self):
        # Finding 2. A stray, unterminated tag line for THIS run -- never
        # produced by _block() itself, only reachable by tampering with an
        # existing block's terminator -- cannot be safely removed, so it
        # sits alongside whatever the current run legitimately writes.
        # That is a harmless, BOUNDED residual, never itself data loss:
        # each recompose still replaces only this run's own properly
        # terminated block, so the leftover is never duplicated further.
        # Order does not matter.
        unterminated = f"{notes._TAG} 2026-09-14 {RUN}]\nOldStaleMetric=0.1"
        terminated = notes._block(RUN, {"Metric": 1.0}, "2026-09-15")
        for text in (
            f"Keep me.\n\n{unterminated}\n\n{terminated}",
            f"Keep me.\n\n{terminated}\n\n{unterminated}",
        ):
            composed = notes.compose(text, RUN, {"Metric": 2.0}, "2026-09-16")
            assert "OldStaleMetric=0.1" in composed
            assert composed.count(notes._TAG) == 2
            # A further recompose does not add a third tag line.
            composed_again = notes.compose(composed, RUN, {"Metric": 3.0}, "2026-09-17")
            assert composed_again.count(notes._TAG) == 2

    def test_two_blocks_from_the_same_run_are_both_removed(self):
        first = notes._block(RUN, VALUES, "2026-09-01")
        second = notes._block(RUN, {"X": 1}, "2026-09-15")
        text = f"{first}\n\n{second}"
        assert notes.strip_block(text, RUN) == ""

    def test_a_body_line_that_itself_begins_with_the_tag_still_ends_the_block(self):
        # A body line starting with the tag reads as the START of a new
        # (second) block, not as part of the first -- the lookahead in the
        # pattern is what enforces this.
        block = notes._block(RUN, VALUES, "2026-09-15")
        text = f"{block}\n{notes._TAG} lookalike, not a real tag]"
        stripped = notes.strip_block(text, RUN)
        assert stripped.strip() == f"{notes._TAG} lookalike, not a real tag]"
