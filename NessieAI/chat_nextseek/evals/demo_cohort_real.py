"""A small registry of REAL sample cohorts pulled live from the production
NExtSEEK instance. This module makes real NExtSEEK API calls and real
protocol-blob downloads — it is NOT a fixture, and it must run inside the
`nextseek` container (that's where API + model credentials live; the host
has neither):

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_cohort_real.py

Adding a third cohort means adding an entry to `COHORTS` below — it is data,
not a new code path.

## granuloma — NHP granuloma, single-cell

`D.SEQ-221031SHA-60-PUB` through `D.SEQ-221031SHA-71-PUB` (12 samples).
`fetch_reporter_metadata` resolves all twelve, returning a full lineage
across sample types A.SCXP, D.SEQ, DNA, NHP, PAV, TIS (non-human primate ->
tissue -> DNA -> sequencing -> single-cell expression). Every D.SEQ carries
`LibraryStrategy: RNAseq`, `LibrarySource: transcriptomic`,
`LibrarySelection: cDNA`, `SequencingType: Single Cell RNA Sequencing`,
`Sequencer: Illumina NovaSeq 6000`, `Protocol:
https://fairdata-dev.mit.edu/sops/76`. Five protocols are discovered in
total, including SOP 76 (Seq-Well) and SOP 13 (Drop-seq alignment cookbook).

`filter_summary_for_deg` leaves only `R_bp` as a D.SEQ grouping candidate for
this cohort — there is no real contrast variable here. That is a true and
useful signal, not a gap to paper over: it means rnasplice should NOT be
recommended for this cohort, because there is no evidence of a two-group
comparison to run it against. Nothing in this module manufactures one.

## macrophage — human macrophage, bulk

`D.SEQ-241219BRY-1-PUB` through `D.SEQ-241219BRY-6-PUB` (6 samples). Sample
types across the lineage: A.GEX (1), CEL (6), D.SEQ (6), DNA (6), PAT (3),
PAV (3), TIS (6); lineage CEL -> DNA -> D.SEQ (sequencing) and
PAT -> PAV -> TIS -> CEL (patient -> visit -> tissue -> cell isolate). Every
D.SEQ carries `SequencingType: RNA-Seq`, `LibraryStrategy: RNA-Seq`,
`LibrarySource: Transcriptomic`, `LibrarySelection: cDNA`, `Sequencer:
NextSeq 500`, `Protocol: https://fairdata-dev.mit.edu/sops/65`. Two protocols
are discovered: SOP 65 (`HMDM-Protocol.docx`, ex vivo macrophage isolation
and RNA-sequencing, deidentified buffy coats from three healthy human donors
obtained from the MGH Blood Center) and SOP 138
(`systematic_SEQ_analysis.docx`).

`filter_summary_for_deg` leaves only `TIS.Parent` as a grouping candidate for
this cohort — every D.SEQ-level field is either uniform or sample-unique.
Bulk, no single-cell resolution, no stated two-group D.SEQ contrast: that
combination of evidence is the whole point of running this cohort alongside
`granuloma` — same question, same evaluator, different real biology.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.sample_digest import build_sample_digest

# ---------------------------------------------------------------------------
# Cohort registry — a third cohort is a new entry here, not a new code path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cohort:
    key: str
    display_name: str
    description: str
    uids: tuple[str, ...]


COHORTS: dict[str, Cohort] = {
    "granuloma": Cohort(
        key="granuloma",
        display_name="NHP granuloma — single-cell",
        description=(
            "Twelve live single-cell RNA-seq samples (Seq-Well / Drop-seq) from a "
            "non-human-primate granuloma lineage, D.SEQ-221031SHA-60-PUB..71-PUB."
        ),
        uids=tuple(f"D.SEQ-221031SHA-{i}-PUB" for i in range(60, 72)),
    ),
    "macrophage": Cohort(
        key="macrophage",
        display_name="Human macrophage — bulk",
        description=(
            "Six live bulk RNA-seq samples from ex vivo human macrophages "
            "(deidentified donor buffy coats), D.SEQ-241219BRY-1-PUB..6-PUB."
        ),
        uids=tuple(f"D.SEQ-241219BRY-{i}-PUB" for i in range(1, 7)),
    ),
}

DEFAULT_COHORT_KEY = "granuloma"

def build_real_digest(config: ChatConfig | None = None, uids: list[str] | None = None) -> dict[str, Any]:
    """Build the sample digest for a real cohort against the live NExtSEEK
    instance. Makes real network calls: sample metadata retrieval, protocol
    record lookup, and protocol blob download + text extraction. Must run
    inside the nextseek container.

    `uids` defaults to the `granuloma` cohort's UIDs when omitted; pass
    `COHORTS[key].uids` explicitly to build any other registered cohort.
    """
    config = config if config is not None else ChatConfig()
    uids = uids if uids is not None else list(COHORTS[DEFAULT_COHORT_KEY].uids)
    return build_sample_digest(config, uids)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=sorted(COHORTS), default=DEFAULT_COHORT_KEY)
    args = parser.parse_args()

    cohort = COHORTS[args.cohort]
    print(json.dumps(build_real_digest(uids=list(cohort.uids)), indent=2))
