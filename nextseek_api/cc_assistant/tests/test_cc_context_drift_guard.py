"""The CC agent's baked context must not drift from its source of truth.

Two copies of the NExtSEEK context pack exist:

* ``chat_nextseek/src/chat_nextseek/context/`` — the source of truth, edited by
  humans and consumed in-process by the ``nextseek_query`` engine.
* ``docker/cc-runtime/build_context/plugins/nextseek/context/`` — a hand-copied
  duplicate baked into the ``dmac-assistant:poc`` image and mounted into every
  ephemeral Container-CC agent as its ground truth for endpoints/vocabulary.

Nothing in the build syncs them, so an edit to one silently leaves the other
behind. That is not merely cosmetic: commit ``03840f0`` ("stop advertising
sample mutation endpoints to the API agent") removed ``POST /samples/``,
``PATCH /samples/{uid}/`` and ``DELETE /samples/{uid}/`` from the source copy,
but the baked copy kept advertising all three to the CC agent for months — a
live privilege regression (#65a).

A third axis is guarded further down: ``read_safe_endpoints.json`` has no
counterpart in the source pack, but it does have one outside both directories —
``nextseek_api/assistant/read_safe_endpoints.json``, the copy the Django write
gate actually loads to permit or block an ``api-read`` op (#83).

These tests are the missing sync check. Hermetic: stdlib only, no docker, no
network, no DB.
"""

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = REPO_ROOT / "chat_nextseek" / "src" / "chat_nextseek" / "context"
BAKED_DIR = (
    REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "context"
)

# ---------------------------------------------------------------------------
# #83: the enforcing copy of the read-safe allowlist
# ---------------------------------------------------------------------------
# read_safe_endpoints.json exists at exactly TWO paths in this repo (the sidecar
# has none — docker/ns-sidecar/app/write_gate.py says so in its first line):
#
#   nextseek_api/assistant/read_safe_endpoints.json   ENFORCED. write_gate's
#       default_allowlist_path() resolves here; build_gate() blocks any api-read
#       whose (endpoint, METHOD) is absent from it.
#   docker/.../plugins/nextseek/context/read_safe_endpoints.json   ADVERTISED.
#       Baked into the agent image; what the agent reads to decide what it
#       believes is read-safe.
#
# The equality set below cannot reach the enforced copy: _shared_names() is an
# INTERSECTION of SOURCE_DIR and BAKED_DIR, and neither is nextseek_api/assistant/.
# So this pair gets its own explicit comparison. If the advertised copy and the
# enforced copy disagree, the agent's belief about what it may call and the gate
# that constrains it are out of step, and nothing else in the tree notices.
ENFORCED_ALLOWLIST = REPO_ROOT / "nextseek_api" / "assistant" / "read_safe_endpoints.json"
BAKED_ALLOWLIST = BAKED_DIR / "read_safe_endpoints.json"

# The baked pack is small and hand-maintained, so it is pinned exactly. Pinning
# it is what makes the equality check below meaningful in BOTH directions: the
# "shared" set is an intersection, so without this, deleting a file from the
# baked dir would shrink the intersection and let the guard pass vacuously.
# Adding a genuinely new file here is a deliberate, reviewed act.
EXPECTED_BAKED_FILES = frozenset({
    "MANIFEST.md",
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_graph_schema.json",
    "min_sampletypes_db.json",
    "neo4j_schema.json",
    "projects_db.json",
    "read_safe_endpoints.json",
})

# Baked-only by design — these have no counterpart in the source pack because
# they exist to steer the *agent*, not the in-process pipeline.
EXPECTED_BAKED_ONLY = frozenset({
    "MANIFEST.md",           # index telling the agent which file to consult when
    "read_safe_endpoints.json",  # write-safety classification, agent-specific
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
# Caveat for anyone testing this guard by deleting a file: three of the source
# pack's files are re-fetched from a live Neo4j and rewritten into this directory
# whenever chat_nextseek's config is loaded and the on-disk copy is not from
# today (chat_nextseek/src/chat_nextseek/config.py:1732 _ensure_schema_file, via
# :1747, :1796, :1841) — neo4j_schema.json (shared, not listed here) plus the
# two listed below, neo4j_assay-sample-conn.json and neo4j_protocol_schema.json.
# Deleting one of those and re-running the suite silently recreates it, so use
# one of the other six entries here to prove this pin bites.
EXPECTED_SOURCE_ONLY = frozenset({
    ".gitignore",                     # not context; _files() uses iterdir(), which keeps dotfiles
    "assays_db.json",                 # full catalog; the agent gets min_assays_db.json instead
    "sampletypes_db.json",            # full catalog; the agent gets min_sampletypes_db.json
    "nextseek_api.yaml",              # full OpenAPI spec; the agent gets min_api_endpoints*.json
    "neo4j_assay-sample-conn.json",   # pipeline-internal graph connectivity map
    "neo4j_protocol_schema.json",     # pipeline-internal protocol schema
    "neo4j_schema_dev.json",          # per-environment snapshots; the agent gets neo4j_schema.json
    "neo4j_schema_prod.json",
})

# ---------------------------------------------------------------------------
# Known, deliberately-unresolved divergence
# ---------------------------------------------------------------------------
# neo4j_schema.json is a point-in-time SNAPSHOT fetched from a live Neo4j, not
# a hand-authored policy file, and the two copies were fetched from what look
# like DIFFERENT graph databases:
#
#   source  fetched_at 2026-05-11, 85 Sample properties
#   baked   fetched_at 2026-04-23, 23 Sample properties
#
# Neither is a superset of the other. The baked copy's 23 Sample properties are
# a strict subset of source's 85, but its *vocabulary* holds entries source
# lacks entirely: investigation "BreakThroughCancer", study "GBM", and 5 assay
# titles (Spatial Transcriptomics Analysis, Long Read Sequencing, ...).
#
# So syncing is not a mechanical copy — it would both add 62 Sample properties
# and DELETE vocabulary the agent currently resolves against. Picking a winner
# requires re-fetching from whichever Neo4j the deployed agent actually queries,
# which cannot be decided from the source tree. Left as-is deliberately; see
# issue #65. This exemption is NOT a licence for the file to drift further —
# see test_known_divergences_still_actually_diverge, which forces the exemption
# to be deleted the moment someone does sync the file.
KNOWN_DIVERGENCES = frozenset({"neo4j_schema.json"})


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


def _files(directory: Path) -> set[str]:
    return {
        p.name
        for p in directory.iterdir()
        if p.is_file() and p.suffix not in _DERIVED_SUFFIXES
    }


def _shared_names() -> list[str]:
    return sorted(_files(SOURCE_DIR) & _files(BAKED_DIR))


def _guarded_names() -> list[str]:
    return [n for n in _shared_names() if n not in KNOWN_DIVERGENCES]


def test_context_dirs_exist():
    assert SOURCE_DIR.is_dir(), f"source context pack missing at {SOURCE_DIR}"
    assert BAKED_DIR.is_dir(), f"baked context pack missing at {BAKED_DIR}"


def test_baked_file_set_is_pinned():
    """Direction 2: the baked pack cannot gain or lose files unreviewed.

    Without this, deleting a baked file would make it non-shared and silently
    exempt it from the equality check below.
    """
    assert _files(BAKED_DIR) == set(EXPECTED_BAKED_FILES)


def test_baked_only_files_are_the_expected_ones():
    baked_only = _files(BAKED_DIR) - _files(SOURCE_DIR)
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
    source_only = _files(SOURCE_DIR) - _files(BAKED_DIR)
    unbaked = sorted(source_only - set(EXPECTED_SOURCE_ONLY))
    vanished = sorted(set(EXPECTED_SOURCE_ONLY) - source_only)
    assert source_only == set(EXPECTED_SOURCE_ONLY), (
        "the source context pack's un-baked file set changed.\n"
        f"  in source, never baked, undeclared: {unbaked or 'none'}\n"
        f"  declared source-only but gone (deleted, or now baked): {vanished or 'none'}\n"
        "For a new file: either copy it into the baked pack and rebuild the "
        "cc-agent image, or add it to EXPECTED_SOURCE_ONLY with the reason the "
        "agent does not need it."
    )


@pytest.mark.parametrize("name", _guarded_names())
def test_shared_context_file_is_identical_to_source(name):
    """Direction 1: every baked file with a source counterpart matches it byte
    for byte. This is the check that would have caught #65a."""
    source = (SOURCE_DIR / name).read_bytes()
    baked = (BAKED_DIR / name).read_bytes()
    assert baked == source, (
        f"{name} has drifted between the source pack and the baked CC copy.\n"
        f"  source: {SOURCE_DIR / name}\n"
        f"  baked:  {BAKED_DIR / name}\n"
        "Nothing syncs these automatically — copy source -> baked and rebuild "
        "the cc-agent image."
    )


@pytest.mark.parametrize("name", sorted(KNOWN_DIVERGENCES))
def test_known_divergences_still_actually_diverge(name):
    """Self-cleaning exemption.

    If someone resolves the neo4j_schema.json snapshot question and syncs the
    file, this fails and forces the stale exemption out of KNOWN_DIVERGENCES,
    so the file rejoins the real guard instead of staying permanently exempt.
    """
    assert name in _shared_names(), f"{name} is exempted but no longer shared"
    source = (SOURCE_DIR / name).read_bytes()
    baked = (BAKED_DIR / name).read_bytes()
    assert baked != source, (
        f"{name} is now in sync — delete it from KNOWN_DIVERGENCES (and this "
        "docstring's rationale) so the drift guard covers it again."
    )


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
    from nextseek_api.assistant import write_gate

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
    from nextseek_api.assistant import write_gate

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


@pytest.mark.parametrize("directory", [SOURCE_DIR, BAKED_DIR], ids=["source", "baked"])
def test_enriched_endpoints_advertise_no_sample_mutations(directory):
    rows = json.loads((directory / "min_api_endpoints_enriched.json").read_text())
    advertised = {(r.get("method", "").upper(), r.get("path", "")) for r in rows}
    leaked = [pair for pair in FORBIDDEN_SAMPLE_MUTATIONS if pair in advertised]
    assert not leaked, (
        f"{directory.name} copy re-advertises sample mutation endpoints to the "
        f"CC agent: {leaked} (removed from the source of truth by 03840f0, #65a)"
    )


# ---------------------------------------------------------------------------
# The write surface advertised by min_api_endpoints.json
# ---------------------------------------------------------------------------
# The UNenriched catalog is a different file from the one guarded above, and it
# does still advertise sample mutations. That is DELIBERATE and was ruled on
# explicitly: the write path is meant to exist, so these rows stay.
#
# What was missing is any statement of WHICH mutations are on offer. The two
# copies are byte-identical, so the equality guard above catches them drifting
# apart -- but it says nothing if someone adds a row to BOTH, which is exactly
# what a sync script or a bulk regeneration would do. A new privileged endpoint
# could therefore reach the agent with nobody having looked at it.
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


def _advertised_mutations(directory: Path) -> set[tuple[str, str]]:
    rows = json.loads((directory / "min_api_endpoints.json").read_text())
    return {
        (r.get("method", "").upper(), r.get("path", ""))
        for r in rows
        if r.get("method", "").upper() in MUTATING_METHODS
    }


@pytest.mark.parametrize("directory", [SOURCE_DIR, BAKED_DIR], ids=["source", "baked"])
def test_advertised_mutating_endpoints_are_pinned(directory):
    """The set of mutating endpoints offered to the agent is exactly the pinned
    set — no silent additions, no silent removals."""
    actual = _advertised_mutations(directory)
    expected = set(ADVERTISED_MUTATIONS)
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    assert actual == expected, (
        f"the mutating endpoints advertised to the CC agent by {directory.name}/"
        f"min_api_endpoints.json changed.\n"
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
SCHEMA_RAG_SERVICE = REPO_ROOT / "nextseek_api" / "schema_rag" / "service.py"
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
