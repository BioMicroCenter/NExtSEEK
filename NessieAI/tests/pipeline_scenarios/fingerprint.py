"""sha256 over everything that decides routing and pipeline choice (spec §4.2).

Covered, as INPUTS groups them: the routing code (the BAML RouteQuery function and its client,
the route capabilities, the router package, the family labels in corpus.json), the pipeline
agent's prompt, tool schemas, loop and catalog, the selection prompt and atlases, and the code
that builds the selection payload (digest, metadata, protocols, schemas, param checks).
The replay serves the recorded router decision and model answers, so this hash is the only
guard on all of it. Not covered: the model id (it is kept in summary.json and each row, so a
swap is visible) and the code that runs after the model answers, which replay runs for real.

A recorded tape is valid only for the fingerprint it was recorded under: a changed input
means the model might now answer differently, so CI fails until a paid run re-records.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_CHAT = "NessieAI/chat_nextseek/src/chat_nextseek/"
INPUTS: tuple[str, ...] = (
    # Routing: the replay serves the recorded decision, so these are its only guard.
    "NessieAI/dmac_assistant/baml_src/router.baml",       # RouteQuery and the types it takes
    "NessieAI/dmac_assistant/baml_src/clients.baml",      # the client RouteQuery names
    "NessieAI/dmac_assistant/build_context/route_capabilities.json",
    "NessieAI/dmac_assistant/src/dmac_assistant/router/*.py",
    "NessieAI/router/router.py",
    "NessieAI/router/router_context.py",
    "NessieAI/router/laya.py",
    "NessieAI/router/laya_common.py",
    "NessieAI/router/laya_options.json",
    "NessieAI/router/policy.py",
    "NessieAI/router/posterior_selector.py",
    "NessieAI/router/followup.py",
    "NessieAI/router/family_labels.py",
    "NessieAI/router/baml_introspect.py",
    "NessieAI/router/run_done.py",
    "NessieAI/tests/nessie_tests/corpus.json",             # the family labels the router reads
    # What the model sees when it chooses a pipeline: prompts, tool schemas, the loop, the atlas
    # and the catalog.
    _CHAT + "prompts/pipeline_agent.txt",
    _CHAT + "pipeline/selection.py",
    _CHAT + "pipeline/selection_context.py",
    _CHAT + "pipeline/agent_tools.py",
    _CHAT + "pipeline/agent.py",
    _CHAT + "seqera/catalog.py",
    _CHAT + "seqera/nfcore_rna_atlas.json",
    _CHAT + "seqera/nfcore_dna_atlas.json",
    _CHAT + "seqera/nfcore_param_atlas.json",
    # The code that builds the selection payload from the cohort's metadata, protocols and schemas.
    _CHAT + "pipeline/sample_digest.py",
    _CHAT + "reports/metadata.py",
    _CHAT + "reports/protocols.py",
    _CHAT + "seqera/nfcore_atlas.py",
    _CHAT + "seqera/nfcore_schema.py",
    _CHAT + "seqera/pipeline_params.py",
    _CHAT + "seqera/schema_check.py",
    _CHAT + "reports/templates/nfcore/*.json",
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
