"""Wave 6: validate the router capability registry against the real shim set.

`dmac_assistant.router.capabilities.load_capabilities` validates the file's
SHAPE (valid JSON, `routes` is a non-empty list, each item matches the
`RouteCapability` model) but never checks that a `route_name` or an advertised
`tool` actually EXISTS. A dead or misspelled route therefore loads silently and
the router advertises a destination it can never dispatch to.

These tests close that gap — the d55cd90 pattern (test the layer nobody tested),
here applied to the config the BAML `RouteQuery` router consumes. They run
against whatever `route_capabilities.json` ships with the package, so a future
edit that breaks the invariants fails CI instead of the live router.
"""
import pathlib

from dmac_assistant.router.agent import _ROUTE_ALIAS
from dmac_assistant.router.capabilities import load_capabilities

# Real routable route aliases. `unrelated` is the fallback and carries no
# capability entry. Source of truth: dmac_assistant/router/agent.py:_ROUTE_ALIAS.
ROUTABLE = {alias for alias in _ROUTE_ALIAS.values() if alias != "unrelated"}

# Curated allowlist of tool / agent / skill names a route may advertise.
#   nextseek_query -> chat_nextseek agents (chat_nextseek/agents/__init__.py,
#                     plus pipeline_agent in agents/planner/tools.py)
#   container_cc   -> CC plugin/skill capability labels (docker/cc-runtime
#                     container/CLAUDE.md skill set)
# Keep in sync with the real agent registry + the CC skill set.
# 2026-07-31: the CC half was wrong. `nextseek-api` and `skill-runner` have never
# shipped — the plugin exposes `nextseek-api-read` / `nextseek-api-write`, and there
# is no skill-runner binary. Advertising a tool the image does not have misleads the
# router on exactly the families that have no coverage, and this allowlist agreeing
# with the wrong value is why CI could not see the drift. Now derived from the bin
# directory so it cannot drift again silently.
_CC_BIN = (pathlib.Path(__file__).resolve().parents[3]
           / "docker/cc-runtime/build_context/plugins/nextseek/bin")
_SHIPPED_OPS = {p.name for p in _CC_BIN.iterdir() if p.name.startswith("nextseek-")} \
    if _CC_BIN.is_dir() else set()

ALLOWED_TOOLS = {
    "entity_agent", "parser_agent", "api_agent", "graph_agent",
    "reporter_agent", "memory_agent", "system_agent", "pipeline_agent",
    "nextseek-batch-upload", "bash", "filesystem",
} | _SHIPPED_OPS


# Every user-facing NS capability must be exercised by a task family, or the router
# has no way to learn that questions of that kind belong to NExtSEEK.
#
# `system_agent` sat in nextseek_query.tools with NO family for the entire life of the
# registry. test_every_advertised_tool_is_real passed the whole time, because it checks
# a tool is in an allowlist and never that anything routes TO it. The result:
# "Tell me about Tissue collection" (task 832) fell to `unrelated` and got the canned
# refusal for a question about assay id 350.
TOOL_TO_FAMILY = {
    "api_agent": "sample_search",
    "graph_agent": "lineage_or_graph",
    "reporter_agent": "reporter_summary",
    "memory_agent": "memory_lookup",
    "system_agent": "system_or_catalog_question",
    "pipeline_agent": "pipeline_build_and_launch",
}

# Always-on pipeline stages rather than destinations a question can be routed to.
# Listed explicitly so a NEW user-facing tool without a family fails this test instead
# of being silently waved through.
INFRASTRUCTURE_TOOLS = {"entity_agent", "parser_agent"}

# container_cc's tools are capabilities its families share rather than destinations a
# question routes to, so a 1:1 tool->family map does not apply there. Several of them
# ARE family-specific in practice (run-ls and build-upload-xlsx belong to
# pipeline_output_reingest, api-write to entity_write_via_api), but the agent picks the
# op from the skill docs at turn time, not from this registry, so pinning a map here
# would assert a coupling that does not exist.
GENERIC_TOOLS = {"nextseek-batch-upload", "bash", "filesystem"} | _SHIPPED_OPS


def _caps():
    return load_capabilities()


def test_registry_loads_and_is_nonempty():
    assert _caps(), "route_capabilities.json produced no routes"


def test_every_route_name_is_a_real_routable_route():
    for c in _caps():
        assert c.route_name in ROUTABLE, (
            f"route '{c.route_name}' is not a routable Route alias "
            f"{sorted(ROUTABLE)} — the router can never dispatch to it (dead route)."
        )


def test_no_duplicate_route_names():
    names = [c.route_name for c in _caps()]
    assert len(names) == len(set(names)), f"duplicate route_name(s): {names}"


def test_every_advertised_tool_is_real():
    for c in _caps():
        for tool in c.tools:
            assert tool in ALLOWED_TOOLS, (
                f"route '{c.route_name}' advertises unknown tool '{tool}' "
                f"(not in the real agent/skill set) — dead capability."
            )


def test_task_families_are_well_formed():
    for c in _caps():
        assert c.task_families, f"route '{c.route_name}' has no task families"
        for tf in c.task_families:
            assert tf.name and tf.name.strip(), (
                f"empty task-family name under {c.route_name}"
            )
            assert tf.description and tf.description.strip(), (
                f"task family '{tf.name}' ({c.route_name}) has no description"
            )
            assert tf.example_queries, (
                f"task family '{tf.name}' ({c.route_name}) has no example_queries"
            )


def test_task_family_names_are_globally_unique():
    # Overlapping-trigger hygiene (recon Section 7.3): two families sharing a name
    # across routes advertise one capability at two opposite destinations.
    all_names = [tf.name for c in _caps() for tf in c.task_families]
    dupes = sorted({n for n in all_names if all_names.count(n) > 1})
    assert not dupes, f"duplicate task_family name(s) across routes: {dupes}"


def test_reingest_carveout_is_distinct_from_batch_upload():
    # Wave 6 carve-out (recon Section 7.1/7.3): registering finished pipeline
    # OUTPUTS as new A.* samples is the `nextseek` skill's job (nextseek-run-ls +
    # nextseek-build-upload-xlsx), NOT nextseek-batch-upload. The registry must
    # advertise them as separate container_cc families so the router's reasoning
    # matches the container/CLAUDE.md carve-out.
    cc = next(c for c in _caps() if c.route_name == "container_cc")
    fam_names = {tf.name for tf in cc.task_families}
    assert {"pipeline_output_reingest", "batch_upload_preparation"} <= fam_names, (
        f"container_cc must advertise both reingest families; found {sorted(fam_names)}"
    )
    bup = next(tf for tf in cc.task_families if tf.name == "batch_upload_preparation")
    joined = " ".join(bup.example_queries).lower()
    assert "reingest" not in joined and "re-ingest" not in joined, (
        "batch_upload_preparation examples still claim reingest — that belongs to "
        "pipeline_output_reingest per the Wave 5 carve-out."
    )


def test_every_user_facing_tool_has_a_task_family():
    """The test that would have caught the orphaned system_agent at CI time.

    `test_every_advertised_tool_is_real` above checks a tool is in an allowlist. It
    never checks the other direction: that some task family actually routes to it. A
    tool nothing routes to is a capability the router cannot reach, which is how
    catalog questions ended up at `unrelated`.
    """
    for c in _caps():
        family_names = {tf.name for tf in c.task_families}
        for tool in c.tools:
            if tool in INFRASTRUCTURE_TOOLS or tool in GENERIC_TOOLS:
                continue
            family = TOOL_TO_FAMILY.get(tool)
            assert family is not None, (
                f"route '{c.route_name}' advertises '{tool}', which is neither mapped "
                f"to a task family in TOOL_TO_FAMILY nor declared infrastructure. "
                f"Add the mapping (and the family) or list it as infrastructure — "
                f"an unmapped user-facing tool is one the router cannot be told about."
            )
            assert family in family_names, (
                f"route '{c.route_name}' advertises '{tool}' but no task family "
                f"'{family}' exercises it. From a cold session nothing tells the "
                f"router that such questions belong here, so they fall to `unrelated`."
            )


def test_nextseek_query_declares_a_catalog_question_family():
    """Catalog questions must have a declared home, with its discriminators neutralised.

    Task 832 ("Tell me about Tissue collection") was refused while 833 ("assays I have
    access to") and 834 ("available to me") passed. The only difference was the
    first-person cue, so the family text has to say that a cue is not required — and
    that an ordinary-sounding biological noun is not evidence of being off-topic,
    because NExtSEEK's registered names ARE ordinary lab vocabulary.
    """
    ns = next(c for c in _caps() if c.route_name == "nextseek_query")
    fam = next((tf for tf in ns.task_families if tf.name == "system_or_catalog_question"), None)

    assert fam is not None, (
        f"nextseek_query has no catalog-question family; found "
        f"{sorted(tf.name for tf in ns.task_families)}"
    )
    assert "system_agent" in ns.tools, "the family exists but the tool it needs is gone"

    text = fam.description.lower()
    assert "system_question" in text, "the downstream mode must be named"
    for cue in ("first-person", "first person"):
        if cue in text:
            break
    else:
        raise AssertionError(
            "the family must state that a first-person cue is NOT required — that cue "
            "is the only thing separating task 832 (refused) from 833/834 (passed)"
        )
    assert "biolog" in text or "vocabulary" in text, (
        "the family must state that ordinary lab vocabulary is not evidence of being "
        "off-topic; NExtSEEK's registered assay names are plain English"
    )


def test_the_blessed_container_cc_families_stay_declared():
    """Entity writes and open-ended analysis routed CC 7/7 with no family backing them.

    The router reasoned ad hoc off `not_for` each time, so any registry edit could flip
    them. They are declared now; this keeps them declared.
    """
    cc = next(c for c in _caps() if c.route_name == "container_cc")
    fam_names = {tf.name for tf in cc.task_families}

    assert {"entity_write_via_api", "open_ended_analysis"} <= fam_names, (
        f"container_cc lost a blessed family; found {sorted(fam_names)}"
    )

    write = next(tf for tf in cc.task_families if tf.name == "entity_write_via_api")
    assert "batch_upload_preparation" in write.description, (
        "entity_write_via_api must distinguish itself from batch_upload_preparation — "
        "one writes to the live API, the other builds a reviewable workbook"
    )


def test_ambiguous_study_resolution_names_the_known_investigations():
    """Its trigger used to be 'the name is not an obvious project' — a negative test
    with no referent, which contradicted itself inside a single run (819 CC vs 817 NS
    for the same GBM scope). The known names must be enumerated."""
    cc = next(c for c in _caps() if c.route_name == "container_cc")
    fam = next(tf for tf in cc.task_families if tf.name == "ambiguous_study_resolution")

    text = fam.description
    for investigation in ("CSBC", "Griffith", "Impact", "MetNet", "SRP", "Shoulders"):
        assert investigation in text, (
            f"investigation '{investigation}' is not enumerated in the trigger, so the "
            f"router must still guess what counts as an 'obvious project'"
        )
