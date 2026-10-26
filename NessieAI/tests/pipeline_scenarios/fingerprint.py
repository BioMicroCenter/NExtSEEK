"""sha256 over everything that decides routing and pipeline choice (spec §4.2).

A recorded tape is valid only for the fingerprint it was recorded under: a changed input
means the model might now answer differently, so CI fails until a paid run re-records.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INPUTS: tuple[str, ...] = (
    "NessieAI/dmac_assistant/baml_src/router.baml",
    "NessieAI/chat_nextseek/src/chat_nextseek/prompts/pipeline_agent.txt",
    "NessieAI/chat_nextseek/src/chat_nextseek/pipeline/selection.py",
    "NessieAI/chat_nextseek/src/chat_nextseek/pipeline/selection_context.py",
    "NessieAI/chat_nextseek/src/chat_nextseek/seqera/nfcore_rna_atlas.json",
    "NessieAI/chat_nextseek/src/chat_nextseek/seqera/nfcore_dna_atlas.json",
    "NessieAI/chat_nextseek/src/chat_nextseek/seqera/nfcore_param_atlas.json",
    "NessieAI/chat_nextseek/src/chat_nextseek/reports/templates/nfcore/*.json",
    "NessieAI/tests/chat_nextseek/fixtures/nfcore/*.nextflow_schema.json",
    "NessieAI/tests/chat_nextseek/fixtures/nfcore/*.schema_input.json",
)


def current() -> str:
    h = hashlib.sha256()
    for pattern in INPUTS:
        for path in sorted(REPO.glob(pattern)):
            h.update(str(path.relative_to(REPO)).encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()
