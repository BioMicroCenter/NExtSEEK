import pytest
from django.contrib.auth import get_user_model

from NessieAI.ns.reingest import manifest, uid_resolve
from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

RUN_DIR = "/net/cluster/runs/nfcore_rnaseq_fixture"
ROWS = [{"sample": "CONTROL_REP1",
         "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz", "fastq_2": ""}]


def _none(_path):
    return []


def test_launch_record_wins_and_is_marked_as_such():
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": None}])
    assert uid_resolve.resolve(ROWS, RUN_DIR, _none) == [
        ("CONTROL_REP1", "D.SEQ-EXAMPLE-1", manifest.RESOLUTION_LAUNCH_RECORD, ())]


def test_falls_back_to_an_exact_fastq_path_match():
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-EXAMPLE-9"])
    assert out == [("CONTROL_REP1", "D.SEQ-EXAMPLE-9", manifest.RESOLUTION_FASTQ_EXACT, ())]


def test_two_candidate_parents_is_ambiguous_and_never_guessed():
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-A", "D.SEQ-B"])
    assert out == [("CONTROL_REP1", None, manifest.RESOLUTION_AMBIGUOUS, ())]


def test_no_candidate_is_unresolved():
    assert uid_resolve.resolve(ROWS, RUN_DIR, _none) == [
        ("CONTROL_REP1", None, manifest.RESOLUTION_UNRESOLVED, ())]


def test_a_multi_run_sample_is_flagged_multirun_not_resolved():
    # No PipelineRun exists for this run_dir, so each row falls to the fastq
    # fallback. The stub answers the same single candidate for every path,
    # so each row resolves unambiguously (on its own) to that UID; both
    # rows resolving to the SAME UID collapses to one parent, not two.
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: ["D.SEQ-A"])
    assert {r[2] for r in out} == {manifest.RESOLUTION_MULTIRUN}
    assert all(r[1] is None for r in out)
    # Each row's OWN fastq path resolves to "D.SEQ-A" (the stub answers the
    # same UID for any path), so BOTH contributing rows resolve to the same
    # UID -- de-duplicated to a single-element parents tuple, not an error.
    assert all(r[3] == ("D.SEQ-A",) for r in out)


def test_a_multirun_samples_own_rows_resolve_independently_by_fastq():
    # Two distinct fastq paths, two distinct D.SEQ candidates: this is the
    # case the fix is for -- a real multi-run sample whose lanes came from
    # two different D.SEQ records, both now recovered.
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    mapping = {
        "/net/cluster/fastq/S1_L001_R1.fastq.gz": ["D.SEQ-LANE-1"],
        "/net/cluster/fastq/S1_L002_R1.fastq.gz": ["D.SEQ-LANE-2"],
    }
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: mapping.get(p, []))
    assert {r[2] for r in out} == {manifest.RESOLUTION_MULTIRUN}
    assert all(r[1] is None for r in out)
    assert all(r[3] == ("D.SEQ-LANE-1", "D.SEQ-LANE-2") for r in out)


def test_a_multirun_sample_partially_resolves_when_only_one_row_matches():
    # The lineage-honesty case the brief calls out explicitly: one
    # contributing row resolves, the other has no candidate at all. The
    # parents tuple is the real, partial result -- not silently emptied, and
    # not padded with a fabricated second UID.
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    mapping = {"/net/cluster/fastq/S1_L001_R1.fastq.gz": ["D.SEQ-LANE-1"]}
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: mapping.get(p, []))
    assert all(r[3] == ("D.SEQ-LANE-1",) for r in out)


def test_a_multirun_rows_own_ambiguous_fastq_match_contributes_nothing():
    # One row's fastq path has two candidates in the D.SEQ database -- never
    # guess, so that row contributes nothing, but the sample's OTHER row
    # (an unambiguous match) still contributes its own UID.
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    mapping = {
        "/net/cluster/fastq/S1_L001_R1.fastq.gz": ["D.SEQ-A", "D.SEQ-B"],
        "/net/cluster/fastq/S1_L002_R1.fastq.gz": ["D.SEQ-LANE-2"],
    }
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: mapping.get(p, []))
    assert all(r[3] == ("D.SEQ-LANE-2",) for r in out)


def test_a_multirun_samples_own_rows_resolve_via_the_launch_record_by_fastq_path():
    # The launch record's cohort has two entries for "S1" -- one per
    # contributing row -- discriminated by fastq_1 rather than by
    # nfcore_sample, since `PipelineRun.uid_for`/`knows_sample` cannot tell
    # the two rows apart by name alone (see uid_resolve._resolve_multirun_row).
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[
            {"d_seq_uid": "D.SEQ-LANE-1", "nfcore_sample": "S1",
             "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": None},
            {"d_seq_uid": "D.SEQ-LANE-2", "nfcore_sample": "S1",
             "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": None},
        ])
    # A fastq stub that would answer wrongly if it were ever consulted --
    # proves the launch record was actually used, not the fastq fallback.
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: ["D.SEQ-WRONG"])
    assert all(r[3] == ("D.SEQ-LANE-1", "D.SEQ-LANE-2") for r in out)


def test_present_in_cohort_with_null_uid_stays_unresolved_without_trying_fastq():
    """knows_sample=True, uid_for=None: the launch already established there
    is nothing to match. A fastq candidate that WOULD match must be ignored --
    this is the step-2 case the brief's original code got wrong by only
    checking `uid_for` truthiness and falling through to the fastq path.
    """
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": None, "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": None}])
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-WOULD-MATCH-IF-TRIED"])
    assert out == [("CONTROL_REP1", None, manifest.RESOLUTION_UNRESOLVED, ())]


def test_a_multirun_rows_own_null_uid_cohort_entry_stays_unresolved_without_trying_fastq():
    """Mirrors test_present_in_cohort_with_null_uid_stays_unresolved_without_trying_fastq
    for the multi-run case: a cohort entry existing for this row's own
    (nfcore_sample, fastq_1) pair with `d_seq_uid: None` means the launch
    already established there is nothing in NExtSEEK to match this row
    against, so `_resolve_multirun_row` must not retry it by fastq path --
    that would manufacture a same-run coincidence for a row the launch
    record explicitly marked unresolvable. The OTHER contributing row (a
    genuine cohort hit, non-null UID) still resolves normally.
    """
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[
            {"d_seq_uid": None, "nfcore_sample": "S1",
             "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": None},
            {"d_seq_uid": "D.SEQ-LANE-2", "nfcore_sample": "S1",
             "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": None},
        ])
    calls: list[str] = []

    def lookup(path):
        calls.append(path)
        return ["D.SEQ-WOULD-MATCH-IF-TRIED"]

    out = uid_resolve.resolve(rows, RUN_DIR, lookup)
    assert {r[2] for r in out} == {manifest.RESOLUTION_MULTIRUN}
    assert all(r[1] is None for r in out)
    # Only the row with a genuine (non-null) cohort UID contributes a
    # parent; the null-UID row contributes nothing.
    assert all(r[3] == ("D.SEQ-LANE-2",) for r in out)
    # The launch record was authoritative for BOTH rows (each matched the
    # cohort on its own (nfcore_sample, fastq_1) pair), so the fastq lookup
    # was never consulted for either -- not even the one whose entry has no
    # UID.
    assert calls == []


def test_absent_from_a_known_cohort_falls_back_to_fastq():
    """knows_sample=False for THIS sample (a launch record exists for the run,
    but its cohort never mentions CONTROL_REP1): this is the step-3 case, and
    must still try the fastq fallback -- unlike the null-UID case above.
    """
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-OTHER", "nfcore_sample": "SOME_OTHER_SAMPLE",
                 "fastq_1": "/net/cluster/fastq/OTHER_R1.fastq.gz",
                 "fastq_2": None}])
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-EXAMPLE-9"])
    assert out == [("CONTROL_REP1", "D.SEQ-EXAMPLE-9", manifest.RESOLUTION_FASTQ_EXACT, ())]


def _by_basename(mapping):
    """A fastq lookup stub that discriminates on its argument: it matches
    only the literal basename string, never the full path `resolve` tries
    first. Exercises the basename fallback tier for real, unlike every other
    stub in this file which returns the same value regardless of argument --
    with those, the exact tier always answers first and the basename branch
    never runs.
    """
    def lookup(path):
        return mapping.get(path, [])
    return lookup


def test_basename_match_resolves_but_is_recorded_as_the_weaker_tier():
    fastq = "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz"
    basename = "CONTROL_REP1_R1.fastq.gz"
    rows = [{"sample": "CONTROL_REP1", "fastq_1": fastq, "fastq_2": ""}]
    out = uid_resolve.resolve(
        rows, RUN_DIR, _by_basename({basename: ["D.SEQ-EXAMPLE-9"]}))
    assert out == [("CONTROL_REP1", "D.SEQ-EXAMPLE-9", manifest.RESOLUTION_FASTQ_BASENAME, ())]


def test_basename_match_with_two_candidates_is_ambiguous_and_never_guessed():
    fastq = "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz"
    basename = "CONTROL_REP1_R1.fastq.gz"
    rows = [{"sample": "CONTROL_REP1", "fastq_1": fastq, "fastq_2": ""}]
    out = uid_resolve.resolve(
        rows, RUN_DIR, _by_basename({basename: ["D.SEQ-A", "D.SEQ-B"]}))
    assert out == [("CONTROL_REP1", None, manifest.RESOLUTION_AMBIGUOUS, ())]


def test_multirun_wins_over_a_resolvable_launch_record():
    """The existing multi-run test (above) never creates a PipelineRun, so it
    only proves multi-run beats the fastq path -- which was already being
    skipped once a launch record exists. This proves the stronger claim: a
    multi-run sample is excluded even when the launch record HAS a valid UID
    for it. Multi-run exclusion is a measurement limit, not an identity one --
    nf-core concatenates those reads before QC, so the single figure it
    reports cannot honestly be attributed to any one contributing sample.
    Knowing the UID does not make the number attributable.
    """
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "S1",
                 "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz",
                 "fastq_2": None}])
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: ["D.SEQ-A"])
    assert {r[2] for r in out} == {manifest.RESOLUTION_MULTIRUN}
    assert all(r[1] is None for r in out)
    # The single-uid field stays None regardless, but the sample's own
    # lineage is still recovered: L001 via the launch record (the cohort
    # entry that exists for it), L002 via the fastq fallback (the cohort has
    # no entry for it at all).
    assert all(r[3] == ("D.SEQ-EXAMPLE-1", "D.SEQ-A") for r in out)
