"""The CC agent's baked context must not drift from its source of truth.

Two directories feed the NExtSEEK context pack the agent reads at
``/app/plugins/nextseek/context/`` in the ``dmac-assistant:poc`` image:

* ``NessieAI/chat_nextseek/src/chat_nextseek/context/``: the source of truth, edited by
  humans and consumed in-process by the ``nextseek_query`` engine.
* ``NessieAI/docker/cc-runtime/build_context/plugins/nextseek/context/``: the plugin
  tree's own context directory, which the image's plugin COPY lays down.

They used to hold two hand-kept copies of the same files, and nothing synced them.
That was not merely cosmetic: commit ``03840f0`` ("stop advertising
sample mutation endpoints to the API agent") removed ``POST /samples/``,
``PATCH /samples/{uid}/`` and ``DELETE /samples/{uid}/`` from the source copy,
but the baked copy kept advertising all three to the CC agent for months — a
live privilege regression (#65a).

Since NessieAI Phase C there is one copy of each shared file: the Dockerfile COPYs
it from the Compose named context ``chat_nextseek`` to its in-image path, and the
plugin tree keeps only the files without a source twin plus min_graph_schema.json,
whose source twin has drifted (below). ``image_context.py`` beside this module replays the
Dockerfile's COPY lines, so every check here reads the file the image really
bakes, whichever directory that is.

A third axis is guarded further down: ``read_safe_endpoints.json`` has no
counterpart in the source pack, but it does have one outside both directories —
``NessieAI/ns/read_safe_endpoints.json``, the copy the Django write
gate actually loads to permit or block an ``api-read`` op (#83).

These tests are the missing sync check. Hermetic: stdlib only, no docker, no
network, no DB.

What they cannot see is the built image itself. They read the files a build
WOULD bake, so an edit to a canonical file followed by an app rebuild alone
(no ``./startup.sh rebuild --component cc-agent``) passes here while the agent
keeps its old copy. The bytes actually baked into the image are compared with
the checkout by the ``cc-agent context`` stack-health check
(``check_cc_agent_context`` in ``startup/steps/validate.py``), after every rebuild.
"""

import ast
import json
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.tests.cc.image_context import (
    canonical_context_copies,
    image_context_files,
    image_context_source,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context"
BAKED_DIR = paths.CC_PLUGIN_DIR / "context"

# ---------------------------------------------------------------------------
# #83: the enforcing copy of the read-safe allowlist
# ---------------------------------------------------------------------------
# read_safe_endpoints.json exists at exactly TWO paths in this repo (the sidecar
# has none: NessieAI/docker/ns-sidecar/app/write_gate.py says so in its first line):
#
#   NessieAI/ns/read_safe_endpoints.json   ENFORCED. write_gate's
#       default_allowlist_path() resolves here; build_gate() blocks any api-read
#       whose (endpoint, METHOD) is absent from it.
#   docker/.../plugins/nextseek/context/read_safe_endpoints.json   ADVERTISED.
#       Baked into the agent image; what the agent reads to decide what it
#       believes is read-safe.
#
# The equality set below cannot reach the enforced copy: _shared_names() is an
# INTERSECTION of SOURCE_DIR and the image's context files, and NessieAI/ns/
# feeds neither.
# So this pair gets its own explicit comparison. If the advertised copy and the
# enforced copy disagree, the agent's belief about what it may call and the gate
# that constrains it are out of step, and nothing else in the tree notices.
ENFORCED_ALLOWLIST = paths.READ_SAFE_ENDPOINTS
BAKED_ALLOWLIST = BAKED_DIR / "read_safe_endpoints.json"

# The baked pack is small and hand-maintained, so it is pinned exactly: the
# files the image's plugin context holds, whichever directory each comes from.
# Pinning it is what makes the equality check below meaningful in BOTH
# directions: the "shared" set is an intersection, so without this, dropping a
# file from the image would shrink the intersection and let the guard pass
# vacuously. Adding a genuinely new file here is a deliberate, reviewed act.
EXPECTED_BAKED_FILES = frozenset({
    "MANIFEST.md",
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_graph_schema.json",
    "min_sampletypes_db.json",
    "ops.json",
    "projects_db.json",
    "read_safe_endpoints.json",
})

# The baked files that are the source file itself: the Dockerfile COPYs each from
# the chat_nextseek named context, so the plugin tree carries no copy to drift.
EXPECTED_FROM_SOURCE = frozenset({
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_sampletypes_db.json",
    "projects_db.json",
})

# What the plugin tree's context directory itself holds: the baked-only files and
# min_graph_schema.json, whose source twin has drifted (phase 12 owns that file; it is
# hand-authored routing prose rather than a schema capture, despite the name).
EXPECTED_PLUGIN_TREE_FILES = EXPECTED_BAKED_FILES - EXPECTED_FROM_SOURCE

# Baked-only by design — these have no counterpart in the source pack because
# they exist to steer the *agent*, not the in-process pipeline.
EXPECTED_BAKED_ONLY = frozenset({
    "MANIFEST.md",           # index telling the agent which file to consult when
    "read_safe_endpoints.json",  # write-safety classification, agent-specific
    "ops.json",  # Plan 005 canonical OpSpec export; not a chat_nextseek context file
})

# Source-only by design — the mirror of EXPECTED_BAKED_ONLY, and the whole point
# of #84. _shared_names() is an INTERSECTION, so a file added to the source pack
# and forgotten in the baked pack is outside the equality check by construction:
# nothing fails, and the agent silently runs without it. EXPECTED_BAKED_FILES
# pins the baked side against that; this pins the source side.
#
# Together the two pins close all four directions:
#   new file in source only  -> here
#   new file in baked only   -> EXPECTED_BAKED_FILES + EXPECTED_BAKED_ONLY
#   new file in both         -> EXPECTED_BAKED_FILES
#   source file deleted      -> here (if source-only) or EXPECTED_BAKED_ONLY (if shared)
#
# Adding a name here is the deliberate act of declaring "the agent does not need
# this"; the alternative is to bake it. All 16 files in the source pack are
# git-tracked, so this set is stable rather than dependent on build artefacts.
#
# The Neo4j-derived files here are no longer refreshed from a live graph: the graph-search
# work removed _fetch_neo4j_schema / _ensure_neo4j_schema / _ensure_schema_file from
# ChatConfig (pinned by NessieAI/tests/chat_nextseek/test_graph_catalog.py), so every name
# below is a committed file that only a hand edit changes. Three database exports in this
# directory ARE still rewritten daily (min_sampletypes_db.json, min_assays_db.json,
# projects_db.json, by _ensure_context_files) — those are baked, so they are not listed here.
EXPECTED_SOURCE_ONLY = frozenset({
    ".gitignore",                     # not context; _files() uses iterdir(), which keeps dotfiles
    "assays_db.json",                 # full catalog; the agent gets min_assays_db.json instead
    "sampletypes_db.json",            # full catalog; the agent gets min_sampletypes_db.json
    "nextseek_api.yaml",              # full OpenAPI spec; the agent gets min_api_endpoints*.json
    "neo4j_assay-sample-conn.json",   # pipeline-internal graph connectivity map
    "neo4j_protocol_schema.json",     # pipeline-internal protocol schema
    # The NS-side fallback the graph agent uses when the live catalog cannot be read
    # (CONTEXT_FALLBACK in agents/graph.py). The CC agent no longer gets a copy: it reads
    # the deployed graph through the nextseek-graph-schema op, which is what
    # test_the_cc_agent_reads_the_graph_schema_live_rather_than_baked below pins.
    "neo4j_schema.json",
    "neo4j_schema_dev.json",          # per-environment snapshots, read by nothing on a turn
    "neo4j_schema_prod.json",
})

# ---------------------------------------------------------------------------
# The graph schema is read live, not baked (Nessie master plan 7.3)
# ---------------------------------------------------------------------------
# neo4j_schema.json used to be baked here as well, and it was exempted from the
# equality check because the two copies were captures of different graphs at
# different times. Measured on 2026-09-17: the baked copy's own fetched_at read
# 2026-04-23 and the source copy's 2026-08-21, while the deployed graph was at
# schema 1.2 — so neither described it, and the baked one described a graph that
# stopped existing at the 2026-08-28 property cleanup. Syncing them would only
# have made them agree on something false.
#
# The file is therefore no longer baked. The agent calls nextseek-graph-schema,
# which reads the live catalog server-side, so a catalog change no longer needs a
# cc-agent rebuild and this class of drift cannot recur. The source copy stays as
# the NS engine's in-process fallback.
#
# Nothing is exempt from the equality check any more: every shared file is compared.


# Suffixes of files that are GENERATED into the context directory at runtime
# rather than authored into it. They are gitignored
# (chat_nextseek/.gitignore:50), so `git status` never shows them, but this
# guard reads the directory rather than the index and would otherwise count
# them as un-baked source files and fail.
#
# `.npz` is the semantic catalog's embedding matrix cache: catalog_semantic.py
# writes `<cache_dir>/<name>.embeddings.npz`, and cache_dir is wired to
# CONTEXT_DIR. Any test that touches the semantic catalog materialises
# `assays.embeddings.npz` and `sampletypes.embeddings.npz` here, so the guard
# passed on a fresh checkout and failed on one that had run the suite a few
# times — a false positive that cost real debugging time.
#
# Excluding them is correct rather than a workaround: this guard is about
# CONTEXT PACK MEMBERSHIP — which authored files did or did not get baked into
# the agent image — and a derived cache is not a pack member. That the cache
# lands in a source-controlled directory at all is a separate defect, tracked
# in issue #102.
_DERIVED_SUFFIXES = frozenset({".npz"})

# Files the config WRITES into the context directory at runtime, excused by exact name.
#
# `labs_db.json` is the labs the daily context export mines from SEEK institution titles
# (chat_nextseek.labs, spec 2026-09-18 section 6.1). It holds real lab titles, so it is
# gitignored (context/.gitignore), excluded from the app build context (.dockerignore) and
# deliberately NOT in CANONICAL_CONTEXT_FILES: the CC route gets lab resolution through the
# entity op, which runs in the app. A checkout that has run the config holds the file, and
# without this exclusion the source-only pin would fail there and pass on a fresh checkout.
_RUNTIME_ONLY_NAMES = frozenset({"labs_db.json"})


def _files(directory: Path) -> set[str]:
    return {
        p.name
        for p in directory.iterdir()
        if p.is_file() and p.suffix not in _DERIVED_SUFFIXES and p.name not in _RUNTIME_ONLY_NAMES
    }


def _shared_names() -> list[str]:
    return sorted(_files(SOURCE_DIR) & image_context_files())


def test_context_dirs_exist():
    assert SOURCE_DIR.is_dir(), f"source context pack missing at {SOURCE_DIR}"
    assert BAKED_DIR.is_dir(), f"baked context pack missing at {BAKED_DIR}"


def test_baked_file_set_is_pinned():
    """Direction 2: the baked pack cannot gain or lose files unreviewed.

    Without this, deleting a baked file would make it non-shared and silently
    exempt it from the equality check below.
    """
    assert image_context_files() == set(EXPECTED_BAKED_FILES)


def test_shared_files_are_baked_from_the_source_copy():
    """One copy: the image takes each of these from the chat_nextseek named
    context, and the plugin tree holds only the files that are not the source's.

    A copy put back in the plugin tree would be overwritten in the image by the
    later named-context COPY, so it could only drift; this fails on it.
    """
    assert canonical_context_copies() == set(EXPECTED_FROM_SOURCE)
    assert _files(BAKED_DIR) == set(EXPECTED_PLUGIN_TREE_FILES), (
        "the plugin tree's context directory changed; a file the image takes from "
        f"the source pack must not be copied back into {BAKED_DIR}"
    )


def test_baked_only_files_are_the_expected_ones():
    baked_only = image_context_files() - _files(SOURCE_DIR)
    assert baked_only == set(EXPECTED_BAKED_ONLY), (
        "a baked context file lost (or gained) its source-of-truth counterpart; "
        "either restore the counterpart or justify it in EXPECTED_BAKED_ONLY"
    )


def test_source_only_files_are_the_expected_ones():
    """Direction 3 (#84): a source file that never got baked cannot hide.

    The equality check is an intersection, so a new file in the source pack that
    nobody copied into the baked pack is compared against nothing and passes
    silently — the agent then runs without context the pipeline has. This is the
    source-side mirror of test_baked_only_files_are_the_expected_ones.
    """
    source_only = _files(SOURCE_DIR) - image_context_files()
    unbaked = sorted(source_only - set(EXPECTED_SOURCE_ONLY))
    vanished = sorted(set(EXPECTED_SOURCE_ONLY) - source_only)
    assert source_only == set(EXPECTED_SOURCE_ONLY), (
        "the source context pack's un-baked file set changed.\n"
        f"  in source, never baked, undeclared: {unbaked or 'none'}\n"
        f"  declared source-only but gone (deleted, or now baked): {vanished or 'none'}\n"
        "For a new file: either bake it (add it to CANONICAL_CONTEXT_FILES in "
        "NessieAI/build_tools/gen_op_surfaces/constants.py, regenerate the "
        "Dockerfile block and rebuild the cc-agent image), or add it to "
        "EXPECTED_SOURCE_ONLY with the reason the agent does not need it."
    )


@pytest.mark.parametrize("name", _shared_names())
def test_shared_context_file_is_identical_to_source(name):
    """Direction 1: every baked file with a source counterpart matches it byte
    for byte. This is the check that would have caught #65a.

    ``image_context_source`` names the file the image's last COPY of that path
    reads. For the files baked from the named context that is the source file
    itself, so this holds by construction; for a plugin-tree file it is a real
    byte comparison.
    """
    source = (SOURCE_DIR / name).read_bytes()
    baked_path = image_context_source(name)
    baked = baked_path.read_bytes()
    assert baked == source, (
        f"{name} has drifted between the source pack and the baked CC copy.\n"
        f"  source: {SOURCE_DIR / name}\n"
        f"  baked:  {baked_path}\n"
        "Nothing syncs a plugin-tree copy: delete it, add the file to the "
        "capabilities-copy block (python -m NessieAI.build_tools.gen_op_surfaces) "
        "and rebuild the cc-agent image."
    )


def test_the_cc_agent_reads_the_graph_schema_live_rather_than_baked():
    """The graph schema reaches the agent as an op, not as a file in its image.

    Three things have to hold together, and any one of them alone is a trap:

    * the image bakes no neo4j_schema.json — a baked copy is a schema capture that
      goes stale the moment the graph is synced, and nothing tells the agent when;
    * the source copy is still there — it is the NS engine's fallback, not dead
      weight, and deleting it would take that fallback with it;
    * nextseek-graph-schema is registered and points at the assistant endpoint that
      reads the live catalog — without it, removing the baked file would leave the
      agent with no schema at all.
    """
    from NessieAI.cc.op_registry.ops import OPS

    assert "neo4j_schema.json" not in image_context_files(), (
        "the graph schema is baked into the cc-agent image again. It cannot be kept "
        "fresh there: use the nextseek-graph-schema op, which reads the live catalog."
    )
    assert (SOURCE_DIR / "neo4j_schema.json").is_file(), (
        "the NS engine's committed fallback is gone; agents/graph.py falls back to it "
        "whenever the live catalog cannot be read (CONTEXT_FALLBACK)"
    )
    op = next((o for o in OPS if o.op_id == "graph-schema"), None)
    assert op is not None, "no graph-schema op: the agent has no way to read the schema"
    assert op.bin_name == "nextseek-graph-schema"
    assert op.assistant_endpoint == "/nextseek_api/assistant/graph-schema/"


# ---------------------------------------------------------------------------
# #83: advertised read-safety must equal enforced read-safety
# ---------------------------------------------------------------------------
# write_gate is imported inside each test rather than at module scope so the
# top of this file stays stdlib-only. write_gate itself pulls in nothing beyond
# json/os/typing, so these tests remain hermetic: no docker, no network, no DB.


def test_write_gate_loads_the_allowlist_this_guard_watches():
    """The guard must watch the file the gate actually reads.

    What this pins is the *code* in write_gate.default_allowlist_path(): edit it
    to point somewhere else and this fails, so the equality check below cannot
    be left silently guarding an allowlist nothing enforces. Both sides are
    static path strings and Path.resolve() does not touch the filesystem, so the
    existence assertion is separate and explicit — without it, deleting the
    allowlist would leave this test green.
    """
    from NessieAI.ns import write_gate

    actual = Path(write_gate.default_allowlist_path()).resolve()
    assert actual == ENFORCED_ALLOWLIST.resolve(), (
        "write_gate.default_allowlist_path() no longer resolves to the file this "
        f"guard compares.\n  gate loads: {actual}\n  guard watches: {ENFORCED_ALLOWLIST}\n"
        "Point ENFORCED_ALLOWLIST at the new location (and check nothing still "
        "reads the old one)."
    )
    assert actual.is_file(), (
        f"the write gate's read-safe allowlist is missing from disk at {actual}. "
        "load_allowlist() raises AllowlistMissingError there, which the viewset "
        "maps to CONFIG_ERROR, so every api-read op fails closed."
    )


def test_enforced_allowlist_matches_the_baked_agent_copy():
    """Byte equality between the enforced copy and the agent's advertised copy.

    Nothing in the build syncs these two, exactly as nothing syncs the two
    context packs above. This is the check that closes #83.
    """
    enforced = ENFORCED_ALLOWLIST.read_bytes()
    baked = BAKED_ALLOWLIST.read_bytes()
    assert baked == enforced, (
        "read_safe_endpoints.json has drifted between the copy the write gate "
        "enforces and the copy baked into the CC agent image.\n"
        f"  enforced: {ENFORCED_ALLOWLIST}\n"
        f"  baked:    {BAKED_ALLOWLIST}\n"
        "Nothing syncs these automatically — reconcile them and rebuild the "
        "cc-agent image."
    )


def test_enforced_and_baked_allowlists_agree_on_endpoint_methods():
    """Semantic diff, so a failure names the endpoints rather than the bytes.

    Deliberately not redundant with the byte check: this one survives a
    whitespace-only reformat and reports exactly which (endpoint, METHOD) pairs
    the agent believes it may call but the gate would block, and vice versa.
    """
    from NessieAI.ns import write_gate

    enforced = write_gate.load_allowlist_from_entries(
        json.loads(ENFORCED_ALLOWLIST.read_text(encoding="utf-8"))
    )
    baked = write_gate.load_allowlist_from_entries(
        json.loads(BAKED_ALLOWLIST.read_text(encoding="utf-8"))
    )
    advertised_but_blocked = sorted(baked - enforced)
    enforced_but_unadvertised = sorted(enforced - baked)
    assert baked == enforced, (
        "the CC agent's advertised read-safe set and the write gate's enforced "
        "set disagree.\n"
        f"  agent believes read-safe, gate would BLOCK: {advertised_but_blocked or 'none'}\n"
        f"  gate permits, agent never told about: {enforced_but_unadvertised or 'none'}"
    )


# ---------------------------------------------------------------------------
# #65a: the specific privilege the drift leaked
# ---------------------------------------------------------------------------
# POST-as-read query endpoints (advanced_search, parents_by_child_types,
# admin/samples/retrieve) are legitimately advertised and deliberately absent
# from this list — see read_safe_endpoints.json for their audited rationale.
FORBIDDEN_SAMPLE_MUTATIONS = (
    ("POST", "/nextseek_api/samples/"),
    ("PATCH", "/nextseek_api/samples/{uid}/"),
    ("DELETE", "/nextseek_api/samples/{uid}/"),
)


def _catalog(which: str, name: str) -> Path:
    """``source``: the source pack's file. ``baked``: the file the image bakes."""
    return SOURCE_DIR / name if which == "source" else image_context_source(name)


@pytest.mark.parametrize("which", ["source", "baked"])
def test_enriched_endpoints_advertise_no_sample_mutations(which):
    catalog = _catalog(which, "min_api_endpoints_enriched.json")
    rows = json.loads(catalog.read_text())
    advertised = {(r.get("method", "").upper(), r.get("path", "")) for r in rows}
    leaked = [pair for pair in FORBIDDEN_SAMPLE_MUTATIONS if pair in advertised]
    assert not leaked, (
        f"{which} copy ({catalog}) re-advertises sample mutation endpoints to the "
        f"CC agent: {leaked} (removed from the source of truth by 03840f0, #65a)"
    )


# ---------------------------------------------------------------------------
# The write surface advertised by min_api_endpoints.json
# ---------------------------------------------------------------------------
# The UNenriched catalog is a different file from the one guarded above, and it
# does still advertise sample mutations. That is DELIBERATE and was ruled on
# explicitly: the write path is meant to exist, so these rows stay.
#
# What was missing is any statement of WHICH mutations are on offer. The image
# bakes the source file itself (one copy), and the equality guard above would
# catch a second copy drifting apart -- but neither says anything when a row is
# added to the one file, which is exactly what a bulk regeneration would do. A
# new privileged endpoint could therefore reach the agent with nobody having
# looked at it.
#
# So the advertised mutating surface is pinned below as data. Any addition OR
# removal fails these tests and forces a human to classify the change.
#
# Only mutating methods are pinned. A new GET is not a privilege change, and
# pinning all 40 rows would turn every routine catalog refresh into a merge
# conflict for no security gain.
MUTATING_METHODS = frozenset({"POST", "PATCH", "DELETE", "PUT"})

# A genuine mutation, advertised on purpose.
WRITE = "write"
# A query endpoint that is POST only because its input is a payload (an
# identifier list, a filter set) rather than a path/query param. Attested
# non-mutating in read_safe_endpoints.json, which records the audited rationale
# and the viewset method verified for each.
POST_AS_READ = "post-as-read"

ADVERTISED_MUTATIONS = {
    ("DELETE", "/nextseek_api/samples/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/assays/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/data_files/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/investigations/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/people/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/projects/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/sample_types/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/samples/{uid}/"): WRITE,
    ("PATCH", "/nextseek_api/sops/{uid}/"): WRITE,
    ("POST", "/nextseek_api/admin/samples/retrieve/"): POST_AS_READ,
    # Additive membership registration. WRITE, not POST_AS_READ: it inserts
    # assay_assets rows. It cannot delete — removal is not expressible in the
    # request shape — but "cannot delete" is not "does not write".
    ("POST", "/nextseek_api/assay-registrations/"): WRITE,
    ("POST", "/nextseek_api/assays/"): WRITE,
    ("POST", "/nextseek_api/data_files/"): WRITE,
    ("POST", "/nextseek_api/investigations/"): WRITE,
    ("POST", "/nextseek_api/people/"): WRITE,
    ("POST", "/nextseek_api/projects/"): WRITE,
    ("POST", "/nextseek_api/sample_types/"): WRITE,
    ("POST", "/nextseek_api/sample_types/get_parents/parents_by_child_types/"): POST_AS_READ,
    ("POST", "/nextseek_api/samples/"): WRITE,
    ("POST", "/nextseek_api/samples/advanced_search/"): POST_AS_READ,
    ("POST", "/nextseek_api/schema_rag/ingest/"): WRITE,
    # #86, audited 2026-08-13: WRITE, not post-as-read. See
    # SCHEMA_RAG_RETRIEVE_AUTO_INGEST below for the finding and the evidence.
    ("POST", "/nextseek_api/schema_rag/retrieve/"): WRITE,
    ("POST", "/nextseek_api/sops/"): WRITE,
}


def _advertised_mutations(catalog: Path) -> set[tuple[str, str]]:
    rows = json.loads(catalog.read_text())
    return {
        (r.get("method", "").upper(), r.get("path", ""))
        for r in rows
        if r.get("method", "").upper() in MUTATING_METHODS
    }


@pytest.mark.parametrize("which", ["source", "baked"])
def test_advertised_mutating_endpoints_are_pinned(which):
    """The set of mutating endpoints offered to the agent is exactly the pinned
    set — no silent additions, no silent removals."""
    catalog = _catalog(which, "min_api_endpoints.json")
    actual = _advertised_mutations(catalog)
    expected = set(ADVERTISED_MUTATIONS)
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    assert actual == expected, (
        f"the mutating endpoints advertised to the CC agent by {which} "
        f"{catalog} changed.\n"
        f"  newly advertised: {added or 'none'}\n"
        f"  no longer advertised: {removed or 'none'}\n"
        "This is the agent's write surface. Classify each change (WRITE / "
        "POST_AS_READ) and update ADVERTISED_MUTATIONS deliberately — do not "
        "just paste the new set in."
    )


def test_post_as_read_endpoints_are_attested_in_the_read_safety_audit():
    """Every endpoint labelled POST_AS_READ is backed by an audit entry.

    Keeps the labels honest: the classification cannot be asserted in this file
    alone, it has to match read_safe_endpoints.json, where each entry records
    the rationale and the viewset method verified non-mutating. The inverse
    guard matters just as much — an endpoint labelled WRITE that turns up in the
    read-safe audit means the two disagree about what it does.
    """
    entries = json.loads((BAKED_DIR / "read_safe_endpoints.json").read_text())
    attested = {
        (method.upper(), e["endpoint"])
        for e in entries
        for method in e.get("methods", [])
    }
    for pair, label in sorted(ADVERTISED_MUTATIONS.items()):
        if label == POST_AS_READ:
            assert pair in attested, (
                f"{pair} is labelled POST_AS_READ but read_safe_endpoints.json "
                "has no entry attesting it non-mutating. Either add the audited "
                "entry or relabel it."
            )
        elif label == WRITE:
            assert pair not in attested, (
                f"{pair} is labelled WRITE but read_safe_endpoints.json attests "
                "it read-safe. One of the two is wrong."
            )


# ---------------------------------------------------------------------------
# #86: the read-safety audit of POST /nextseek_api/schema_rag/retrieve/
# ---------------------------------------------------------------------------
# The endpoint used to carry a third label, POST_AS_READ_UNATTESTED: it reads
# like a query, sits next to the three POSTs the 2026-07-06 audit cleared, and
# that audit never covered it. #86 asked for the audit to be done and the
# endpoint either attested in read_safe_endpoints.json or dropped from the
# agent's context.
#
# Audited 2026-08-13. It is NOT read-safe, so it is labelled WRITE above and
# deliberately stays out of read_safe_endpoints.json. retrieve_endpoints
# auto-ingests: given a schema_url whose session is missing or expired — the
# first-call case, since RetrieveRequest accepts schema_url with no session_id
# (nextseek_api/models.py:2123-2136) — it calls ingest_schema, the same function
# behind POST /schema_rag/ingest/, which this table already labels WRITE. That
# path deletes .duckdb files (session.py:188 cleanup_expired_sessions), performs
# an uncredentialed server-side HTTP GET of the caller-supplied URL (the fetch
# #94 is about), creates a DuckDB file and inserts rows (session.py:73
# create_session, db.py:31 init_session_db, db.py:92 insert_endpoints).
# Corroborated by
# nextseek_api/tests/test_schema_rag_retrieve_coverage.py::TestRetrieveEndpointsAutoIngest.
#
# Attesting it read-safe would have un-gated all of that for the agent's
# api-read op, which write_gate blocks today precisely because the endpoint is
# absent from the allowlist. Removing it from min_api_endpoints.json instead is
# a maintainer ruling, not a test change, and is left open.
SCHEMA_RAG_RETRIEVE = ("POST", "/nextseek_api/schema_rag/retrieve/")
SCHEMA_RAG_SERVICE = paths.NESSIE_ROOT / "schema_rag" / "service.py"
SCHEMA_RAG_RETRIEVE_FN = "retrieve_endpoints"
SCHEMA_RAG_INGEST_FN = "ingest_schema"


def test_schema_rag_retrieve_is_classified_write():
    """The audit's conclusion, pinned so it cannot be quietly softened."""
    assert ADVERTISED_MUTATIONS[SCHEMA_RAG_RETRIEVE] == WRITE, (
        "POST /schema_rag/retrieve/ was audited on 2026-08-13 and found to "
        "auto-ingest (see the comment above). Re-labelling it as a read "
        "requires re-doing that audit, not editing this line."
    )


def _called_function_names(node: ast.AST) -> set[str]:
    """Every function name called anywhere inside ``node``.

    Handles both bare calls (``ingest_schema(...)``) and attribute calls
    (``service.ingest_schema(...)``).
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_schema_rag_retrieve_still_auto_ingests():
    """Self-cleaning attestation, like test_known_divergences_still_actually_diverge.

    The WRITE label above rests on one fact: retrieve_endpoints itself calls
    ingest_schema. If that call is ever removed, this fails and forces the
    classification to be re-derived rather than silently inherited from an audit
    whose premise no longer holds.

    Parsed with ``ast`` rather than imported, so this module stays stdlib-only
    and pulls in no Django settings. ``ast`` is also what makes the check honest:
    an earlier text-slice version of this test could be satisfied by a call in a
    *neighbouring* function or by a commented-out line. The parse scopes the
    search to this one function definition, and comments are not in the tree.
    """
    tree = ast.parse(SCHEMA_RAG_SERVICE.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == SCHEMA_RAG_RETRIEVE_FN
    ]
    assert definitions, (
        f"no module-level {SCHEMA_RAG_RETRIEVE_FN}() in {SCHEMA_RAG_SERVICE} — "
        "re-run the read-safety audit of POST /schema_rag/retrieve/ against "
        "whatever replaced it."
    )
    called = set()
    for definition in definitions:
        called |= _called_function_names(definition)
    assert SCHEMA_RAG_INGEST_FN in called, (
        f"{SCHEMA_RAG_RETRIEVE_FN}() no longer calls {SCHEMA_RAG_INGEST_FN}(), "
        "which is the whole basis for classifying POST /schema_rag/retrieve/ as "
        "WRITE above.\n"
        "Re-run the read-safety audit: if it is now genuinely non-mutating it "
        "can become POST_AS_READ with an entry in read_safe_endpoints.json; if "
        "the call merely moved into a helper it is still a write. Until someone "
        "checks, leave it WRITE."
    )


def test_generated_embedding_caches_do_not_count_as_context_files(tmp_path):
    """A runtime-generated cache must not be mistaken for an un-baked source file.

    catalog_semantic.py writes `<name>.embeddings.npz` into CONTEXT_DIR, so any
    checkout that has run the suite accumulates them. They are gitignored, so
    `git status` is clean and the failure looks like a real drift regression.
    Without the suffix filter in `_files`, `test_source_only_files_are_the_
    expected_ones` fails on a working checkout and passes on a fresh one.
    """
    (tmp_path / "capabilities.md").write_text("authored")
    (tmp_path / "assays.embeddings.npz").write_bytes(b"\x00generated")
    (tmp_path / "sampletypes.embeddings.npz").write_bytes(b"\x00generated")

    assert _files(tmp_path) == {"capabilities.md"}


def test_the_real_context_dir_has_no_undeclared_generated_files(tmp_path):
    """The suffix filter is a declared allowance, not a blanket 'ignore junk'.

    If some other generator starts writing a new artifact type into the context
    pack, that must surface as a drift failure rather than being silently
    absorbed — so this pins that `.npz` is the only extension being excused.
    """
    on_disk = {p.suffix for p in SOURCE_DIR.iterdir() if p.is_file()}
    excused = on_disk & _DERIVED_SUFFIXES
    assert excused <= {".npz"}, (
        f"a new generated artifact type appeared in the context pack: {excused}. "
        "Decide whether it belongs in the pack (bake it) or is derived "
        "(add it to _DERIVED_SUFFIXES with the reason)."
    )


def test_the_runtime_labs_file_does_not_count_as_a_context_file(tmp_path):
    """labs_db.json is written into CONTEXT_DIR by the daily export and never baked.

    It carries real lab titles mined from SEEK, so it is gitignored and excluded from
    the build context (spec 2026-09-18, section 6.1). A checkout that has run the config
    holds it, and without the name exclusion in `_files` the source-only pin fails there
    and passes on a fresh checkout, exactly as the `.npz` caches once did.
    """
    (tmp_path / "capabilities.md").write_text("authored")
    (tmp_path / "labs_db.json").write_text('{"version": 1, "labs": []}')

    assert _files(tmp_path) == {"capabilities.md"}


def test_the_labs_file_is_excused_by_name_only(tmp_path):
    """The exclusion is one runtime-only name, not every `*_db.json`."""
    (tmp_path / "labs_db.json").write_text("{}")
    (tmp_path / "projects_db.json").write_text("[]")
    (tmp_path / "other_labs_db.json").write_text("{}")

    assert _files(tmp_path) == {"projects_db.json", "other_labs_db.json"}
