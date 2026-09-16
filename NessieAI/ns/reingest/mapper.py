"""Apply a pipeline map to a manifest, and name everything it could not map.

Resolution order is committed map rule, then approved database rule, then
unmapped. A committed, code-reviewed map rule is the stronger authority: if it
already produced a value for an attribute, an approved rule must not overwrite
it — it only fills gaps the committed rule left. A PENDING proposal is never
applied here at all: it is evidence shown to the agent, which must re-affirm
it, so an unreviewed rule cannot quietly become permanent by repetition.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from NessieAI.ns.reingest import manifest, maps

ORIGIN_MAP = "map"
ORIGIN_APPROVED = "approved"
ORIGIN_PROPOSED = "proposed"
ORIGIN_PARKED = "parked"

# A sample whose UID resolved this way cannot carry a QC backfill row.
_NO_BACKFILL = {
    manifest.RESOLUTION_MULTIRUN,
    manifest.RESOLUTION_AMBIGUOUS,
    manifest.RESOLUTION_UNRESOLVED,
}


class MappedAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: str
    value: object
    origin: str
    raw_key: str = ""
    source_file: str = ""


class MappedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_type: str
    uid: str | None = None
    nfcore_sample: str = ""
    attributes: dict[str, MappedAttribute] = Field(default_factory=dict)


class MapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[MappedRow] = Field(default_factory=list)
    unmapped: list[dict] = Field(default_factory=list)


def apply(run_manifest: manifest.RunManifest, pipeline_map: maps.PipelineMap,
          approved_rules: dict[str, maps.AttributeRule] | None = None) -> MapResult:
    approved_rules = approved_rules or {}
    result = MapResult()
    ruled_out = pipeline_map.ruled_out()
    metrics_source = run_manifest.sources.get("metrics", "")

    # Every raw key either kind of rule claims -- whether or not it ends up
    # applied to a given sample -- is known evidence, not an unmapped key.
    all_rules = list(pipeline_map.qc_attributes.values()) + list(approved_rules.values())
    claimed = {rule.from_key for rule in all_rules}
    claimed |= {alt for rule in all_rules for alt in rule.alternates}

    for sample in run_manifest.samples:
        if sample.uid_resolution not in _NO_BACKFILL and sample.d_seq_uid:
            row = MappedRow(sample_type="D.SEQ", uid=sample.d_seq_uid,
                            nfcore_sample=sample.nfcore_sample)

            # Committed map rules go first and claim their attributes outright.
            for attribute, rule in pipeline_map.qc_attributes.items():
                found = _lookup(rule, run_manifest, sample)
                if found is None:
                    continue
                value, raw_key = found
                row.attributes[attribute] = MappedAttribute(
                    attribute=attribute, value=value, origin=ORIGIN_MAP,
                    raw_key=raw_key, source_file=metrics_source)

            # Approved rules only fill attributes the committed map left empty.
            for attribute, rule in approved_rules.items():
                if attribute in row.attributes:
                    continue
                found = _lookup(rule, run_manifest, sample)
                if found is None:
                    continue
                value, raw_key = found
                row.attributes[attribute] = MappedAttribute(
                    attribute=attribute, value=value, origin=ORIGIN_APPROVED,
                    raw_key=raw_key, source_file=metrics_source)

            if row.attributes:
                result.rows.append(row)

        for raw_key, value in sample.metrics.items():
            if raw_key in claimed or raw_key in ruled_out:
                continue
            if any(u["raw_key"] == raw_key for u in result.unmapped):
                continue
            result.unmapped.append({
                "raw_key": raw_key, "example_value": value,
                "source_file": metrics_source,
                "example_sample": sample.nfcore_sample,
            })

    for rule in pipeline_map.outputs:
        row = MappedRow(sample_type=rule.sample_type)
        # The output rule's own attributes are the more specific authority:
        # they must win over a provenance attribute of the same name (e.g.
        # A.ALN's "Software" is the STAR version string; provenance's
        # "Software" is the whole software_versions dict).
        for attribute, ref in {**pipeline_map.provenance_attributes,
                               **rule.attributes}.items():
            value = maps.resolve_ref(ref, run_manifest)
            if value is None or value == "":
                continue
            row.attributes[attribute] = MappedAttribute(
                attribute=attribute, value=value, origin=ORIGIN_MAP,
                raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
                source_file=run_manifest.sources.get("params", ""))
        if row.attributes:
            result.rows.append(row)

    return result


def _lookup(rule: maps.AttributeRule, run_manifest: manifest.RunManifest,
            sample: manifest.SampleRecord) -> tuple[object, str] | None:
    """(value, raw_key) from the rule's primary key, then each alternate."""
    for key in [rule.from_key, *rule.alternates]:
        if key.startswith("$"):
            value = maps.resolve_ref(key, run_manifest, sample)
        else:
            value = sample.metrics.get(key)
        if value is not None and value != "":
            return value, key
    return None
