from pathlib import Path

import pytest

from NessieAI.ns.reingest import derived

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfcore_rnaseq_run"

# Applied per-test (not module-wide) so tests that use only inline text still
# RUN where the fixture is absent (e.g. CI), instead of being skipped along
# with the fixture-reading ones. Matches the pattern in test_parsers.py.
needs_fixture = pytest.mark.skipif(
    not FIXTURE.is_dir(),
    reason="nf-core run fixture is local-only; see the fixture README")


def test_derived_metric_names_are_a_closed_set():
    assert derived.DERIVED_METRICS == (
        "cds_pct", "utr_pct", "intron_pct", "intergenic_pct",
        "exon_intron_ratio", "exon_intergenic_ratio", "sense_antisense_ratio",
        "genes_detected", "genes_detected_percent", "top30_count_percent",
        "unaligned_reads",
    )


def test_parse_read_distribution_sums_both_utrs():
    text = (
        "Total Reads                   1000\n"
        "Group               Total_bases   Tag_count   Tags/Kb\n"
        "CDS_Exons           100           600         1.0\n"
        "5'UTR_Exons         10            100         1.0\n"
        "3'UTR_Exons         10            100         1.0\n"
        "Introns             50            150         1.0\n"
        "TSS_up_10kb         10            25          1.0\n"
        "TES_down_10kb       10            25          1.0\n"
    )
    dist = derived.parse_read_distribution(text)
    assert dist["cds_pct"] == 60.0
    assert dist["utr_pct"] == 20.0      # 5' + 3' summed into one slot
    assert dist["intron_pct"] == 15.0
    assert dist["intergenic_pct"] == 5.0


def test_intergenic_pct_uses_widest_window_only_not_sum_of_nested_windows():
    # RSeQC's TSS_up_*/TES_down_* windows are NESTED, not disjoint. The real
    # CONTROL_REP1 fixture
    # (star_salmon/rseqc/read_distribution/CONTROL_REP1.read_distribution.txt)
    # reports TSS_up_1kb=39621, TSS_up_5kb=125048, TSS_up_10kb=198592, and
    # TES_down_1kb=116078, TES_down_5kb=243536, TES_down_10kb=333881 -- each
    # wider window CONTAINS the narrower one's reads. Summing every group
    # whose name starts with TSS_up_/TES_down_ (the naive rule) triple-counts
    # the innermost reads. Only the widest window per side is real.
    text = (
        "Total Reads                   1000000\n"
        "Group               Total_bases   Tag_count   Tags/Kb\n"
        "CDS_Exons           100           600000      1.0\n"
        "5'UTR_Exons         10            50000       1.0\n"
        "3'UTR_Exons         10            50000       1.0\n"
        "Introns             50            100000      1.0\n"
        "TSS_up_1kb          10            39621       1.0\n"
        "TSS_up_5kb          10            125048      1.0\n"
        "TSS_up_10kb         10            198592      1.0\n"
        "TES_down_1kb        10            116078      1.0\n"
        "TES_down_5kb        10            243536      1.0\n"
        "TES_down_10kb       10            333881      1.0\n"
    )
    dist = derived.parse_read_distribution(text)

    total = (600000 + 50000 + 50000 + 100000
              + 39621 + 125048 + 198592 + 116078 + 243536 + 333881)
    sum_everything = 39621 + 125048 + 198592 + 116078 + 243536 + 333881
    widest_only = 198592 + 333881

    buggy_pct = round(sum_everything / total * 100, 4)
    correct_pct = round(widest_only / total * 100, 4)

    assert dist["intergenic_pct"] == correct_pct
    assert dist["intergenic_pct"] != buggy_pct


@needs_fixture
def test_parse_read_distribution_reads_the_real_fixture():
    text = (FIXTURE / "star_salmon" / "rseqc" / "read_distribution"
            / "CONTROL_REP1.read_distribution.txt").read_text()
    dist = derived.parse_read_distribution(text)

    # Hand-tallied straight from the file's own Tag_count column, so this
    # cross-checks the parser against the real numbers rather than a
    # separately-guessed expectation.
    total = (33590473 + 649712 + 9042771 + 3551566
             + 39621 + 125048 + 198592 + 116078 + 243536 + 333881)
    assert dist["cds_pct"] == round(33590473 / total * 100, 4)
    assert dist["utr_pct"] == round((649712 + 9042771) / total * 100, 4)
    assert dist["intron_pct"] == round(3551566 / total * 100, 4)
    assert dist["intergenic_pct"] == round((198592 + 333881) / total * 100, 4)


def test_parse_infer_experiment_maps_strand_patterns_to_forward_reverse():
    # The two "explained by" lines carry strand-pattern strings as their
    # labels, not the words "forward"/"reverse": RSeQC's own convention is
    # that "1++,1--,2+-,2-+" means forward and "1+-,1-+,2++,2--" means
    # reverse.
    text = (
        "\n\n"
        "This is PairEnd Data\n"
        'Fraction of reads failed to determine: 0.1000\n'
        'Fraction of reads explained by "1++,1--,2+-,2-+": 0.4500\n'
        'Fraction of reads explained by "1+-,1-+,2++,2--": 0.4500\n'
    )
    out = derived.parse_infer_experiment(text)
    assert out == {"failed": 0.1, "forward": 0.45, "reverse": 0.45}


@needs_fixture
def test_parse_infer_experiment_reads_the_real_fixture():
    text = (FIXTURE / "star_salmon" / "rseqc" / "infer_experiment"
            / "CONTROL_REP1.infer_experiment.txt").read_text()
    out = derived.parse_infer_experiment(text)
    assert out == {"failed": 0.0954, "forward": 0.4533, "reverse": 0.4513}


def test_exon_ratios_use_cds_plus_utr_over_the_other_class():
    out = derived.compute(
        read_distribution={"cds_pct": 60.0, "utr_pct": 20.0,
                           "intron_pct": 16.0, "intergenic_pct": 4.0},
        infer_experiment=None, star=None, counts=None, n_annotated_genes=None)
    assert out["exon_intron_ratio"] == 5.0        # (60+20)/16
    assert out["exon_intergenic_ratio"] == 20.0   # (60+20)/4


def test_compute_merges_read_distribution_percentages_into_output():
    # cds_pct/utr_pct/intron_pct/intergenic_pct are produced by
    # parse_read_distribution, not by compute()'s own arithmetic -- but
    # compute() must still carry them into its result so a manifest's
    # sample.derived (and a map's $derived.cds_pct) can see all eleven
    # DERIVED_METRICS names, not just the seven compute() calculates.
    out = derived.compute(
        read_distribution={"cds_pct": 60.0, "utr_pct": 20.0,
                           "intron_pct": 16.0, "intergenic_pct": 4.0},
        infer_experiment=None, star=None, counts=None, n_annotated_genes=None)
    assert out["cds_pct"] == 60.0
    assert out["utr_pct"] == 20.0
    assert out["intron_pct"] == 16.0
    assert out["intergenic_pct"] == 4.0


def test_every_derived_metric_name_can_appear_in_compute_output():
    counts = {f"g{i}": float(i) for i in range(1, 41)}
    out = derived.compute(
        read_distribution={"cds_pct": 60.0, "utr_pct": 20.0,
                           "intron_pct": 16.0, "intergenic_pct": 4.0},
        infer_experiment={"forward": 0.6, "reverse": 0.4, "failed": 0.0},
        star={"total": 1000.0, "unique": 800.0, "multi": 150.0},
        counts=counts, n_annotated_genes=80)
    assert set(derived.DERIVED_METRICS) <= set(out.keys())


def test_gene_metrics_count_nonzero_rows_and_top30_share():
    counts = {f"g{i}": float(i) for i in range(1, 41)}   # g1..g40, g0 absent
    counts["zero"] = 0.0
    out = derived.compute(read_distribution=None, infer_experiment=None,
                          star=None, counts=counts, n_annotated_genes=80)
    assert out["genes_detected"] == 40.0
    assert out["genes_detected_percent"] == 50.0
    # top 30 of 1..40 are 11..40 = 765; total 1..40 = 820
    assert round(out["top30_count_percent"], 2) == round(765 / 820 * 100, 2)


def test_unaligned_reads_is_total_minus_unique_minus_multi():
    out = derived.compute(read_distribution=None, infer_experiment=None,
                          star={"total": 1000.0, "unique": 800.0, "multi": 150.0},
                          counts=None, n_annotated_genes=None)
    assert out["unaligned_reads"] == 50.0


def test_a_missing_input_yields_no_key_rather_than_a_zero():
    out = derived.compute(None, None, None, None, None)
    assert out == {}


def test_zero_denominator_is_omitted_not_infinite():
    out = derived.compute(
        read_distribution={"cds_pct": 60.0, "utr_pct": 20.0,
                           "intron_pct": 0.0, "intergenic_pct": 0.0},
        infer_experiment=None, star=None, counts=None, n_annotated_genes=None)
    assert "exon_intron_ratio" not in out
    assert "exon_intergenic_ratio" not in out


def test_sense_antisense_ratio_zero_denominator_is_omitted():
    out = derived.compute(
        read_distribution=None,
        infer_experiment={"forward": 0.5, "reverse": 0.0}, star=None,
        counts=None, n_annotated_genes=None)
    assert "sense_antisense_ratio" not in out
