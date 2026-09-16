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


def test_prose_written_with_no_blank_line_above_it_is_treated_as_block_body_and_lost():
    # Documented limit, not a surprise: a line right under the block with no
    # blank line separating it is indistinguishable from a block value line,
    # so it is consumed (and dropped) along with the block on recompose.
    once = notes.compose("Keep me.", RUN, VALUES, "2026-09-15")
    no_blank_line = f"{once}\nCurator added later."
    twice = notes.compose(no_blank_line, RUN, VALUES, "2026-09-16")
    assert "Curator added later." not in twice


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
