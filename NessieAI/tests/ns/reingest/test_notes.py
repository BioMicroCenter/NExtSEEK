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
