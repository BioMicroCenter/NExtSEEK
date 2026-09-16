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


def test_an_old_style_block_with_no_terminator_falls_back_to_the_blank_line_rule():
    # Data written before the terminator existed has none. strip_block must
    # still bound it correctly, via the original blank-line contract.
    old_style = f"[nfcore-reingest 2026-09-15 {RUN}]\nOldMetric=1.0"
    text = f"Keep me.\n\n{old_style}\n\nCurator prose below a blank line."
    twice = notes.compose(text, RUN, VALUES, "2026-09-16")
    assert "Curator prose below a blank line." in twice
    assert twice.count("[nfcore-reingest") == 1
    assert "2026-09-16" in twice
    assert "OldMetric=1.0" not in twice  # old run's own block is gone


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
