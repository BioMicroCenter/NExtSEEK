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
ALLOWED_TOOLS = {
    "entity_agent", "parser_agent", "api_agent", "graph_agent",
    "reporter_agent", "memory_agent", "system_agent", "pipeline_agent",
    "nextseek-api", "nextseek-batch-upload", "bash", "filesystem", "skill-runner",
}


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
