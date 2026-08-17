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

These tests are the missing sync check. Hermetic: stdlib only, no docker, no
network, no DB.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = REPO_ROOT / "chat_nextseek" / "src" / "chat_nextseek" / "context"
BAKED_DIR = (
    REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "context"
)

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
    "ops.json",
    "projects_db.json",
    "read_safe_endpoints.json",
})

# Baked-only by design — these have no counterpart in the source pack because
# they exist to steer the *agent*, not the in-process pipeline.
EXPECTED_BAKED_ONLY = frozenset({
    "MANIFEST.md",           # index telling the agent which file to consult when
    "read_safe_endpoints.json",  # write-safety classification, agent-specific
    "ops.json",  # Plan 005 canonical OpSpec export; not a chat_nextseek context file
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


def _files(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir() if p.is_file()}


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
# Reads as a query endpoint, but read_safe_endpoints.json carries NO entry for
# it, so nothing has formally attested it. Called out as its own label rather
# than folded into POST_AS_READ: writing it there would manufacture an
# attestation the audit never made. See the test below.
POST_AS_READ_UNATTESTED = "post-as-read-unattested"

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
    ("POST", "/nextseek_api/schema_rag/retrieve/"): POST_AS_READ_UNATTESTED,
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


def test_the_unattested_post_as_read_endpoint_is_still_unattested():
    """Self-cleaning exemption, like KNOWN_DIVERGENCES above.

    POST schema_rag/retrieve/ runs a semantic search over an already-ingested
    OpenAPI schema and returns matching endpoints. By behaviour it is a query,
    and it sits next to the three POSTs the 2026-07-06 read-safety audit did
    clear — but that audit never covered it, so no attestation exists. It is
    advertised to the agent regardless.

    This fails the moment someone audits it and adds the entry, forcing the
    label to be corrected to POST_AS_READ instead of the exemption quietly
    outliving the gap it describes.
    """
    unattested = sorted(
        pair for pair, label in ADVERTISED_MUTATIONS.items()
        if label == POST_AS_READ_UNATTESTED
    )
    assert unattested == [("POST", "/nextseek_api/schema_rag/retrieve/")]
    entries = json.loads((BAKED_DIR / "read_safe_endpoints.json").read_text())
    attested = {
        (method.upper(), e["endpoint"])
        for e in entries
        for method in e.get("methods", [])
    }
    for pair in unattested:
        assert pair not in attested, (
            f"{pair} now HAS a read-safety attestation — relabel it "
            "POST_AS_READ and delete this test."
        )
