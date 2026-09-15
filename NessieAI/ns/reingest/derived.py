"""Metrics computed from parsed inputs, because no nf-core file states them.

This is the ONLY place reingest does arithmetic on a measurement. Map files
reference these by name (`$derived.<name>`) and cannot compute anything
themselves, which is what keeps a map file incapable of surprising behaviour.

A metric whose inputs are missing is OMITTED. Returning 0.0 would be a claim
that the measurement was made and came out zero. A zero denominator omits
the ratio for the same reason, rather than producing infinity or crashing.
"""
from __future__ import annotations

import re

DERIVED_METRICS: tuple[str, ...] = (
    # The four read-distribution percentages are produced by
    # parse_read_distribution (MultiQC/RSeQC report tag COUNTS per group,
    # and D.SEQ has one UTR slot where RSeQC reports two), not by compute()'s
    # own arithmetic. compute() still merges them into its result so that
    # sample.derived (and a map's $derived.cds_pct) carries all eleven names.
    "cds_pct",
    "utr_pct",
    "intron_pct",
    "intergenic_pct",
    "exon_intron_ratio",
    "exon_intergenic_ratio",
    "sense_antisense_ratio",
    "genes_detected",
    "genes_detected_percent",
    "top30_count_percent",
    "unaligned_reads",
)

_GROUP_ROW = re.compile(r"^(\S+)\s+\d+\s+(\d+)\s")

# RSeQC's TSS_up_*/TES_down_* windows are NESTED, not disjoint: the real
# CONTROL_REP1 fixture (star_salmon/rseqc/read_distribution/
# CONTROL_REP1.read_distribution.txt) reports TSS_up_1kb=39621,
# TSS_up_5kb=125048, TSS_up_10kb=198592 -- each wider window CONTAINS the
# narrower one's reads (same shape on the TES_down_ side). Summing every
# group whose name starts with TSS_up_/TES_down_ therefore triple-counts the
# innermost reads. The widest window already includes every narrower one,
# so it alone is "intergenic" on that side.
# Do NOT "fix" this back to summing every TSS_up_*/TES_down_* group -- that
# reintroduces the triple-count. See test_derived.py
# test_intergenic_pct_uses_widest_window_only_not_sum_of_nested_windows.
_INTERGENIC_GROUPS = ("TSS_up_10kb", "TES_down_10kb")

# infer_experiment.txt's two "explained by" lines are labelled with RSeQC's
# strand-pattern strings, not the words "forward"/"reverse".
_FORWARD_PATTERN = "1++,1--,2+-,2-+"
_REVERSE_PATTERN = "1+-,1-+,2++,2--"
_FRACTION_LINE = re.compile(r'^(.*?):\s*([0-9.]+)\s*$')


def parse_read_distribution(text: str) -> dict[str, float]:
    """RSeQC read_distribution.txt -> the four percentages D.SEQ has slots for.

    RSeQC reports 5'UTR and 3'UTR separately and D.SEQ has one UTR attribute,
    so the two are summed here; the sum is what UTRPercent means.
    Intergenic is TSS_up_10kb + TES_down_10kb, the widest window on each
    side -- see the _INTERGENIC_GROUPS comment above for why the narrower
    nested windows are excluded.
    """
    tags: dict[str, int] = {}
    for line in text.splitlines():
        match = _GROUP_ROW.match(line.strip())
        if match:
            tags[match.group(1)] = int(match.group(2))
    total = sum(tags.values())
    if not total:
        return {}

    def pct(count: int) -> float:
        return round(count / total * 100, 4)

    utr = tags.get("5'UTR_Exons", 0) + tags.get("3'UTR_Exons", 0)
    intergenic = sum(tags.get(group, 0) for group in _INTERGENIC_GROUPS)
    return {
        "cds_pct": pct(tags.get("CDS_Exons", 0)),
        "utr_pct": pct(utr),
        "intron_pct": pct(tags.get("Introns", 0)),
        "intergenic_pct": pct(intergenic),
    }


def parse_infer_experiment(text: str) -> dict[str, float]:
    """RSeQC infer_experiment.txt -> {"failed", "forward", "reverse"}.

    The two "explained by" lines carry strand-pattern strings as their
    labels: "1++,1--,2+-,2-+" is forward, "1+-,1-+,2++,2--" is reverse (see
    the real CONTROL_REP1 fixture at
    star_salmon/rseqc/infer_experiment/CONTROL_REP1.infer_experiment.txt).
    A key is present only if its line was found.
    """
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        match = _FRACTION_LINE.match(line)
        if not match:
            continue
        label, value = match.group(1), float(match.group(2))
        if "failed to determine" in label:
            out["failed"] = value
        elif _FORWARD_PATTERN in label:
            out["forward"] = value
        elif _REVERSE_PATTERN in label:
            out["reverse"] = value
    return out


def compute(
    read_distribution: dict[str, float] | None,
    infer_experiment: dict[str, float] | None,
    star: dict[str, float] | None,
    counts: dict[str, float] | None,
    n_annotated_genes: int | None,
) -> dict[str, float]:
    """Every derived metric whose inputs are present. Missing inputs -> missing keys."""
    out: dict[str, float] = {}

    if read_distribution:
        # Pass the four percentages straight through so they end up in
        # sample.derived alongside the ratios computed from them -- see the
        # DERIVED_METRICS comment above.
        for key in ("cds_pct", "utr_pct", "intron_pct", "intergenic_pct"):
            if key in read_distribution:
                out[key] = read_distribution[key]

        exonic = read_distribution.get("cds_pct", 0.0) + read_distribution.get("utr_pct", 0.0)
        intron = read_distribution.get("intron_pct", 0.0)
        intergenic = read_distribution.get("intergenic_pct", 0.0)
        if intron:
            out["exon_intron_ratio"] = round(exonic / intron, 4)
        if intergenic:
            out["exon_intergenic_ratio"] = round(exonic / intergenic, 4)

    if infer_experiment:
        forward = infer_experiment.get("forward", 0.0)
        reverse = infer_experiment.get("reverse", 0.0)
        if reverse:
            out["sense_antisense_ratio"] = round(forward / reverse, 4)

    if star:
        total = star.get("total", 0.0)
        if total:
            unaligned = total - star.get("unique", 0.0) - star.get("multi", 0.0)
            out["unaligned_reads"] = round(max(unaligned, 0.0), 4)

    if counts:
        values = sorted(v for v in counts.values() if v > 0)
        detected = len(values)
        out["genes_detected"] = float(detected)
        if n_annotated_genes:
            out["genes_detected_percent"] = round(detected / n_annotated_genes * 100, 4)
        total_counts = sum(values)
        if total_counts:
            out["top30_count_percent"] = round(sum(values[-30:]) / total_counts * 100, 4)

    return out
