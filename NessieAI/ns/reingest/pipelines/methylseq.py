"""nf-core/methylseq's own "@" tokens: the conversion chemistry and the library protocol,
read from the run's boolean presets (`special_library_types` in the 4.2.0
nextflow_schema.json). A map can only look a param up, and these labels depend on which
of several flags is set, so they are computed here."""
from __future__ import annotations

# (param, label), first set flag wins; the order is the precedence.
_CONVERSION = (("taps", "TAPS"), ("em_seq", "EM-seq"))
_PROTOCOL = (("rrbs", "RRBS"), ("pbat", "PBAT"), ("single_cell", "single-cell"), ("nomeseq", "NOMe-seq"))

#: Every param these tokens read; the test checks each is in the pinned schema.
FLAGS_READ = tuple(name for name, _ in _CONVERSION + _PROTOCOL)


def _is_set(params: dict, name: str) -> bool:
    """A Nextflow boolean param: JSON true, or the string "true" when a launch passed it as text."""
    value = params.get(name)
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _first_set(flags, default: str):
    def compute(run_manifest, sample, context) -> str | None:
        params = run_manifest.params or {}
        return next((label for name, label in flags if _is_set(params, name)), default)
    return compute


COMPUTED = {
    # Bisulfite is methylseq's default chemistry: neither --taps nor --em_seq.
    "conversion_method": _first_set(_CONVERSION, "Bisulfite"),
    # WGBS is the default protocol: none of the protocol presets.
    "methylation_protocol": _first_set(_PROTOCOL, "WGBS"),
}
