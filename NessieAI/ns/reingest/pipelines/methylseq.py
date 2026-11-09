"""nf-core/methylseq's own "@" tokens: the conversion chemistry and the library protocol,
read from the run's boolean params in the 4.2.0 nextflow_schema.json: `taps`, `em_seq`,
`rrbs`, `pbat` and `single_cell` are in `special_library_types`, `nomeseq` in
`bismark_options`. A map can only look a param up, and these labels depend on which of
several flags is set, so they are computed here."""
from __future__ import annotations

# (param, label), first set flag wins; the order is the precedence.
# --taps with a Bismark aligner is still labelled TAPS, though Bismark calls it as
# bisulfite data, so its calls come out inverted: methylseq only logs a recommendation
# to use bwa-mem (workflows/methylseq/main.nf, the `params.taps && params.aligner !=
# 'bwamem'` check). The reingest_maps README says so.
_CONVERSION = (("taps", "TAPS"), ("em_seq", "EM-seq"))
# The protocol label alone, in this order:
# - single_cell together with nomeseq is labelled single-cell only;
# - --nomeseq also makes Bismark's extractor call every context (--CX), which
#   MethylationProtocol does not record;
# - --slamseq, and the trimming-kit presets (--accel, --zymo, ...), set no protocol here,
#   so such a run falls through to WGBS.
_PROTOCOL = (("rrbs", "RRBS"), ("pbat", "PBAT"), ("single_cell", "single-cell"), ("nomeseq", "NOMe-seq"))

#: Every param these tokens read; the test checks each is in the pinned schema.
FLAGS_READ = tuple(name for name, _ in _CONVERSION + _PROTOCOL)


def _is_set(params: dict, name: str) -> bool:
    """A Nextflow boolean param: JSON true, or the string "true" when a launch passed it as text."""
    value = params.get(name)
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _first_set(flags, default: str):
    def compute(run_manifest, sample, context) -> str | None:
        params = run_manifest.params
        if not params:
            # No params file was read, so the run says nothing about its chemistry or
            # protocol; the default would look measured (pipelines/README.md).
            return None
        return next((label for name, label in flags if _is_set(params, name)), default)
    return compute


COMPUTED = {
    # Bisulfite is methylseq's default chemistry: neither --taps nor --em_seq.
    "conversion_method": _first_set(_CONVERSION, "Bisulfite"),
    # WGBS is the default protocol: none of the protocol flags.
    "methylation_protocol": _first_set(_PROTOCOL, "WGBS"),
}
