"""Every committed map must be answerable by the schema, at CI time.

A map naming an attribute that does not exist would otherwise surface as a
failed upload in front of whoever ran the reingest. That is the invariant
this file enforces: reingest never invents a sample attribute.

The schema check runs against a committed SNAPSHOT
(fixtures/sampletype_attributes.json), because CI has no populated
database -- ``attributes_for()`` reads the ``Sample_types_context`` table,
not a file. A snapshot can drift from the real schema, which would make
this gate pass while the thing it guards is broken -- worse than no gate,
because it manufactures confidence. Two mitigations:

1. The fixture carries its own provenance (what generated it, from where,
   and the exact command to refresh it) -- see its ``_provenance`` block.
2. The ``test_live_*`` variants below re-run the same checks against the
   REAL catalog via ``nextseek_api.services.reingest_lookups``, and
   ``pytest.mark.skipif`` themselves out whenever that catalog is empty
   (the sqlite-backed CI lane). In the dev container or the full MySQL
   lane they actually verify the snapshot still matches reality.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import derived, harvest, maps

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sampletype_attributes.json"
_FIXTURE = json.loads(FIXTURE_PATH.read_text())
ATTRS = _FIXTURE["sample_types"]
ALL_MAPS = maps.available()

# Every check below is parametrized over ALL_MAPS. pytest collects
# @parametrize("name", []) as ONE SKIPPED test -- not a failure, not an error --
# and most CI does not fail a build on skips. So a bad glob, a moved directory
# or a deleted map would turn this entire file green while checking nothing,
# which is the precise failure this gate exists to prevent, applied to itself.
def test_there_are_maps_to_check():
    assert ALL_MAPS, (
        f"no committed pipeline maps found in {maps.MAPS_DIR} -- every contract "
        "check in this file is parametrized over that list, so an empty one "
        "silently skips the whole suite instead of failing it")

RUN_SECTIONS = ("params", "pipeline", "software_versions", "outputs")
SAMPLE_SECTIONS = ("metrics", "derived")

# The only names "$outputs.<key>" may resolve at runtime -- anything else
# resolves to None (see maps.resolve_ref / RunManifest.named_outputs) and the
# attribute silently never appears. Read from harvest.py's own matcher table
# rather than retyped here, so the two can never drift apart.
NAMED_OUTPUT_KEYS = {key for key, _ in harvest._NAMED_OUTPUT_MATCHERS}

# The live variant of the schema check. known_sample_types() and
# attributes_for() never raise (see reingest_lookups.py's module docstring:
# "a missing table or row costs the caller an empty catalog, never an
# exception"), so calling them at import time is safe in every lane -- in
# the sqlite-backed CI lane the underlying table simply does not exist yet
# and this comes back empty.
from nextseek_api.services.reingest_lookups import (  # noqa: E402
    attributes_for as _live_attributes_for,
    known_sample_types as _live_known_sample_types,
)

LIVE_SAMPLE_TYPES = _live_known_sample_types()
_LIVE_SKIP_REASON = (
    "no live catalog in this lane (known_sample_types() returned nothing); "
    "only the committed snapshot in fixtures/sampletype_attributes.json is "
    "being checked here, not the real schema"
)


def _live_attribute_titles(sample_type: str) -> set[str]:
    return {a["title"] for a in _live_attributes_for(sample_type)}


@pytest.mark.parametrize("name", ALL_MAPS)
def test_map_parses(name):
    assert maps.load(name).pipeline


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_qc_attribute_target_exists_on_its_sample_type(name):
    for attribute, rule in maps.load(name).qc_attributes.items():
        assert rule.target in ATTRS, f"{name}: unknown sample type {rule.target}"
        assert attribute in ATTRS[rule.target], \
            f"{name}: {rule.target} has no attribute {attribute!r}"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_accepts_parent_types_is_never_empty(name):
    """`maps.PipelineMap.accepts_parent_types` already enforces `min_length=1`
    at load time -- a map JSON that declares `[]` fails `maps.load()` itself
    with a pydantic ValidationError, before this test body ever runs. This
    is a second, explicit, friendlier-message layer of the same invariant:
    an empty list is illegal, not a silent no-op, because
    `test_every_accepts_parent_type_is_a_known_sample_type` below is
    parametrized OVER the list's own entries -- exactly like
    `test_there_are_maps_to_check` above, an empty list would make that
    check "pass" by iterating zero times, silently checking nothing.
    """
    assert maps.load(name).accepts_parent_types, (
        f"{name}: accepts_parent_types must declare at least one sample "
        "type -- an empty list would make every check below pass "
        "vacuously, and would scope the fastq-path parent lookup to search "
        "nothing")


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_accepts_parent_type_is_a_known_sample_type(name):
    for sample_type in maps.load(name).accepts_parent_types:
        assert sample_type in ATTRS, \
            f"{name}: unknown sample type {sample_type!r} in accepts_parent_types"


def test_every_accepts_parent_type_is_a_known_sample_type_catches_a_typo(monkeypatch):
    """Runs the REAL check above (not a re-implementation of its assertion)
    against a fabricated map with a typo'd sample type, to prove the gate
    actually fails CI rather than merely looking like it would. `maps.load`
    is monkeypatched -- rather than adding a fake file under MAPS_DIR -- to
    a stand-in that ignores its `name` argument and always returns the bad
    map, so this stays a unit test of the check, not a fixture the real
    ALL_MAPS list has to carry around.
    """
    bad_map = maps.PipelineMap(
        pipeline="nf-core/fake-for-test", accepts_parent_types=["D.SEQ", "A.NOPE"])
    assert "A.NOPE" not in ATTRS, (
        "the catalog snapshot must not genuinely contain this sentinel "
        "type, or this test cannot tell a real gap from a coincidence")
    monkeypatch.setattr(maps, "load", lambda name: bad_map)
    with pytest.raises(AssertionError, match="A.NOPE"):
        test_every_accepts_parent_type_is_a_known_sample_type("nf-core/fake-for-test")


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_output_rule_names_a_known_sample_type(name):
    for rule in maps.load(name).outputs:
        assert rule.sample_type in ATTRS, \
            f"{name}: unknown sample type {rule.sample_type}"
        for attribute in rule.attributes:
            assert attribute in ATTRS[rule.sample_type], \
                f"{name}: {rule.sample_type} has no attribute {attribute!r}"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_provenance_attribute_exists_on_every_opted_in_output_sample_type(name):
    """provenance_attributes has no target of its own: mapper.apply() merges
    every one of them into every output rule's row THAT OPTS IN
    (``rule.include_provenance``; see OutputRule.include_provenance in
    maps.py), per-sample and per-run alike -- a rule that does not opt in
    gets only its own attributes. So each provenance key must exist as a
    real attribute on every sample type an OPTED-IN output rule declares. An
    override in ``rule.attributes`` for the same key still leaves that key
    in the merged row -- only the source ref changes -- so the check applies
    regardless of which rule attributes happen to override a provenance one.

    A map whose provenance_attributes is non-empty but where NO rule opts in
    is itself a bug worth failing loudly on: that block would be merged into
    nothing, which is either dead configuration or a forgotten
    ``include_provenance: true`` on the rule it was meant for -- either way
    a human should see it, not have it silently do nothing.
    """
    pipeline_map = maps.load(name)
    opted_in_types = {rule.sample_type for rule in pipeline_map.outputs
                       if rule.include_provenance}
    if not pipeline_map.outputs:
        # Checked BEFORE skipping: a map with no output rules at all but a
        # non-empty provenance block is the same dead config as one where
        # nobody opted in, and must fail rather than escape as a skip.
        assert not pipeline_map.provenance_attributes, (
            f"{name}: provenance_attributes is non-empty but the map declares no "
            "output rules at all, so it is merged into nothing")
        pytest.skip(f"{name}: no output rules, so provenance_attributes targets nothing")
    if pipeline_map.provenance_attributes:
        assert opted_in_types, (
            f"{name}: provenance_attributes is non-empty but no output rule "
            "sets include_provenance=true, so it is merged into nothing -- "
            "opt the intended rule in or delete the block")
    for attribute in pipeline_map.provenance_attributes:
        for target in opted_in_types:
            assert target in ATTRS, f"{name}: unknown sample type {target}"
            assert attribute in ATTRS[target], \
                f"{name}: provenance attribute {attribute!r} is merged into " \
                f"every opted-in {target} output row but {target} has no " \
                f"such attribute"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_dollar_ref_names_a_real_manifest_section(name):
    pipeline_map = maps.load(name)
    refs = list(pipeline_map.provenance_attributes.values())
    refs += [v for rule in pipeline_map.outputs for v in rule.attributes.values()]
    refs += [rule.from_key for rule in pipeline_map.qc_attributes.values()
             if rule.from_key.startswith("$")]
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith("$"):
            continue
        section = ref[1:].split(".")[0]
        assert section in RUN_SECTIONS + SAMPLE_SECTIONS, \
            f"{name}: {ref} names no manifest section"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_outputs_dollar_ref_names_a_real_named_output(name):
    """A "$outputs.<key>" ref is only checked above for naming a real
    section ("outputs" is one of RUN_SECTIONS). That is not enough: the key
    itself must be one harvest.py's _NAMED_OUTPUT_MATCHERS actually
    populates in RunManifest.named_outputs, or resolve_ref() returns None at
    runtime and the attribute silently never appears on any row.
    """
    pipeline_map = maps.load(name)
    refs = list(pipeline_map.provenance_attributes.values())
    refs += [v for rule in pipeline_map.outputs for v in rule.attributes.values()]
    refs += [rule.from_key for rule in pipeline_map.qc_attributes.values()
             if rule.from_key.startswith("$outputs.")]
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith("$outputs."):
            continue
        key = ref[len("$outputs."):]
        assert key in NAMED_OUTPUT_KEYS, \
            f"{name}: {ref} names no known named output " \
            f"(see harvest._NAMED_OUTPUT_MATCHERS); it would resolve to None " \
            f"at runtime and the attribute would silently never appear"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_derived_ref_names_a_real_derived_metric(name):
    for rule in maps.load(name).qc_attributes.values():
        if rule.from_key.startswith("$derived."):
            metric = rule.from_key.split(".", 1)[1]
            assert metric in derived.DERIVED_METRICS, \
                f"{name}: unknown derived metric {metric!r}"


@pytest.mark.parametrize("name", ALL_MAPS)
def test_every_ruling_explains_itself(name):
    for ruling in maps.load(name).deliberately_unmapped:
        assert len(ruling.why) > 20, \
            f"{name}: ruling for {ruling.key!r} needs a real reason"


# --- Live variants: the same shape of check, against the real catalog. ---
# These skip themselves out whenever there is no populated database to ask
# (the sqlite-backed CI lane), and actually run in the dev container or the
# full MySQL lane -- the only environments where the snapshot's staleness
# could otherwise go unnoticed.

@pytest.mark.skipif(not LIVE_SAMPLE_TYPES, reason=_LIVE_SKIP_REASON)
@pytest.mark.parametrize("name", ALL_MAPS)
def test_live_every_qc_attribute_target_exists_on_its_sample_type(name):
    for attribute, rule in maps.load(name).qc_attributes.items():
        assert rule.target in LIVE_SAMPLE_TYPES, \
            f"{name}: unknown sample type {rule.target} (live catalog)"
        assert attribute in _live_attribute_titles(rule.target), \
            f"{name}: {rule.target} has no attribute {attribute!r} (live catalog)"


@pytest.mark.skipif(not LIVE_SAMPLE_TYPES, reason=_LIVE_SKIP_REASON)
@pytest.mark.parametrize("name", ALL_MAPS)
def test_live_every_accepts_parent_type_is_a_known_sample_type(name):
    for sample_type in maps.load(name).accepts_parent_types:
        assert sample_type in LIVE_SAMPLE_TYPES, \
            f"{name}: unknown sample type {sample_type!r} in " \
            f"accepts_parent_types (live catalog)"


@pytest.mark.skipif(not LIVE_SAMPLE_TYPES, reason=_LIVE_SKIP_REASON)
@pytest.mark.parametrize("name", ALL_MAPS)
def test_live_every_output_rule_names_a_known_sample_type(name):
    for rule in maps.load(name).outputs:
        assert rule.sample_type in LIVE_SAMPLE_TYPES, \
            f"{name}: unknown sample type {rule.sample_type} (live catalog)"
        titles = _live_attribute_titles(rule.sample_type)
        for attribute in rule.attributes:
            assert attribute in titles, \
                f"{name}: {rule.sample_type} has no attribute {attribute!r} (live catalog)"


@pytest.mark.skipif(not LIVE_SAMPLE_TYPES, reason=_LIVE_SKIP_REASON)
@pytest.mark.parametrize("name", ALL_MAPS)
def test_live_every_provenance_attribute_exists_on_every_opted_in_output_sample_type(name):
    pipeline_map = maps.load(name)
    opted_in_types = {rule.sample_type for rule in pipeline_map.outputs
                       if rule.include_provenance}
    if not pipeline_map.outputs:
        # Checked BEFORE skipping: a map with no output rules at all but a
        # non-empty provenance block is the same dead config as one where
        # nobody opted in, and must fail rather than escape as a skip.
        assert not pipeline_map.provenance_attributes, (
            f"{name}: provenance_attributes is non-empty but the map declares no "
            "output rules at all, so it is merged into nothing")
        pytest.skip(f"{name}: no output rules, so provenance_attributes targets nothing")
    if pipeline_map.provenance_attributes:
        assert opted_in_types, (
            f"{name}: provenance_attributes is non-empty but no output rule "
            "sets include_provenance=true, so it is merged into nothing "
            "(live catalog) -- opt the intended rule in or delete the block")
    for attribute in pipeline_map.provenance_attributes:
        for target in opted_in_types:
            assert target in LIVE_SAMPLE_TYPES, \
                f"{name}: unknown sample type {target} (live catalog)"
            assert attribute in _live_attribute_titles(target), \
                f"{name}: provenance attribute {attribute!r} is merged into " \
                f"every opted-in {target} output row but {target} has no " \
                f"such attribute (live catalog)"
