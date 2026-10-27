"""Check a run's final params against the pipeline's own pinned schema.

build_run_params validates against a hand-written curated menu: a parameter is
admitted if its NAME is on the menu, and its VALUE is checked only when the menu
marks it an enum. Everything else — bools, numbers, paths — reaches params.yml
unexamined. nf-schema aborts a run on any parameter it does not recognise, so a
menu that has drifted from the pipeline does not produce a conversation error,
it produces a dead job on the cluster minutes later.

The schema CHECKS the menu; it does not replace it. The curated menu is
deliberately narrower than the schema — it is what we are willing to expose —
and a schema-OPTIONAL parameter can still be functionally required and silently
wrong when unset (viralrecon's platform/protocol, metatdenovo's orf_caller).
Nothing here adds a schema default or any other parameter to a run.

It also reports a schema-REQUIRED parameter the run neither sets nor has filled
before nf-schema validates: chipseq requires fasta, which only a genome or an explicit
path fills. Filled means a schema default (nextflow.config sets it; the only use made
of schema defaults here), the launcher's own input/outdir, or a reference file a
genome key names.

An unreachable schema is never an error. It returns a skip reason, the caller
validates on the curated menu alone as it always did, and says so.

Type validation handles both string and list-valued `type` properties, which occur
in the six pinned pipelines: `help` is `["boolean", "string"]` in hlatyping and
smrnaseq, and `dfam_version` / `pfam_version` are `["number", "null"]` in rnafusion.
A value must satisfy at least one declared type.
"""
from __future__ import annotations

from typing import Any, Callable

from .catalog import NFCORE_PIPELINE_CATALOG
from .nfcore_schema import get_schema

#: Value checks by JSON Schema type. `bool` is excluded from the numeric checks
#: explicitly: in Python `isinstance(True, int)` is True, so without this a
#: boolean would silently satisfy an integer parameter.
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}

#: Params the launcher writes into every params.yml itself (emitter.emit_luria_launch_artifacts
#: and emit_launch_artifacts), so the run never needs them from the menu.
LAUNCHER_PARAMS = frozenset({"input", "outdir"})

#: Params a genome key fills before the pipeline validates its params, in a pipeline whose
#: schema declares `genome`: main.nf reads them from the iGenomes entry (getGenomeAttribute),
#: and on Luria the run script passes the local --fasta/--gtf beside --genome
#: (luria.run_script.render_run_script).
GENOME_FILLS = frozenset({"fasta", "gtf"})


def schema_properties(nextflow_schema: dict[str, Any]) -> dict[str, dict]:
    """Flatten a nextflow_schema.json into {param_name: property}.

    nf-core groups parameters under `$defs` (rnaseq, hlatyping, smrnaseq,
    rnafusion) or `definitions` (scrnaseq, rnasplice) — both spellings occur
    among the six pinned pipelines, so both are read. Top-level `properties` is
    absent in all six but is read too, because the spec allows it.
    """
    props: dict[str, dict] = {}
    props.update(nextflow_schema.get("properties") or {})
    for container in ("$defs", "definitions"):
        for group in (nextflow_schema.get(container) or {}).values():
            if isinstance(group, dict):
                props.update(group.get("properties") or {})
    return {k: v for k, v in props.items() if isinstance(v, dict)}


def schema_required(nextflow_schema: dict[str, Any]) -> list[str]:
    """Every param the schema lists as required, top level and per group, in schema order."""
    names = list(nextflow_schema.get("required") or [])
    for container in ("$defs", "definitions"):
        for group in (nextflow_schema.get(container) or {}).values():
            if isinstance(group, dict):
                names += group.get("required") or []
    return list(dict.fromkeys(n for n in names if isinstance(n, str)))


def _load_schema(pipeline_key: str, revision: str | None,
                 schema_getter) -> tuple[dict[str, Any] | None, dict[str, dict] | None, str | None]:
    """Return (nextflow_schema, properties, skip_reason): both or the reason."""
    if not revision:
        return None, None, (f"no pinned revision for {pipeline_key!r}, so its schema could "
                            "not be checked")
    get = schema_getter or get_schema
    try:
        documents = get(pipeline_key, revision)
    except Exception as exc:  # noqa: BLE001 - never block a build on a fetch
        return None, None, f"{pipeline_key}@{revision} schema unavailable ({exc})"
    nextflow_schema = (documents or {}).get("nextflow_schema") or {}
    props = schema_properties(nextflow_schema)
    if not props:
        # An empty flatten means the document was not what we expected, not that
        # the pipeline has no parameters. Rejecting every param on that basis
        # would break every build for this pipeline.
        return None, None, (f"{pipeline_key}@{revision} schema declared no parameters, "
                            "so it was not used")
    return nextflow_schema, props, None


def _load_properties(pipeline_key: str, revision: str | None,
                     schema_getter) -> tuple[dict[str, dict] | None, str | None]:
    """Return (properties, skip_reason). Exactly one is None."""
    _schema, props, skip = _load_schema(pipeline_key, revision, schema_getter)
    return props, skip


def missing_required(pipeline_key: str, revision: str | None, params: dict[str, Any],
                     *, schema_getter=None) -> tuple[list[str], str | None]:
    """Schema-required params that `params`, the run's whole param set, neither sets nor fills.

    Filled: a schema default (the pipeline's nextflow.config sets it), LAUNCHER_PARAMS, and
    GENOME_FILLS when the run names a genome and the schema declares one. A None value sets
    nothing. Returns (names, skip_reason); when the schema is unavailable, names is empty.
    """
    nextflow_schema, props, skip = _load_schema(pipeline_key, revision, schema_getter)
    if props is None:
        return [], skip
    given = {k for k, v in (params or {}).items() if v is not None}
    filled = given | LAUNCHER_PARAMS | (GENOME_FILLS if "genome" in given and "genome" in props
                                        else set())
    return [name for name in schema_required(nextflow_schema)
            if name not in filled and (props.get(name) or {}).get("default") is None], None


def genome_fills(pipeline_key: str, revision: str | None, *, schema_getter=None) -> frozenset[str]:
    """GENOME_FILLS when the pinned schema declares a `genome` param, else nothing."""
    _schema, props, _skip = _load_schema(pipeline_key, revision, schema_getter)
    return GENOME_FILLS if props and "genome" in props else frozenset()


def required_message(pipeline_key: str, revision: str | None, name: str,
                     *, by_genome: bool = False) -> str:
    """The error for a required param the run leaves unset; `by_genome` when a genome fills it."""
    via = " (a genome fills it)" if by_genome else ""
    return (f"param {name!r} is required by nf-core/{pipeline_key}@{revision} and this run "
            f"neither sets nor fills it{via}. A run without it fails the pipeline's own "
            "parameter check.")


def check_params(pipeline_key: str, revision: str | None, params: dict[str, Any],
                 *, schema_getter=None, source: str = "curated",
                 required: bool = False) -> tuple[list[str], str | None]:
    """Check every final param against the pinned schema, and that none it requires is unset.

    Returns (errors, skip_reason). When skip_reason is set, errors is empty and
    the caller should validate on the curated menu alone.

    `source` only changes the wording of the "unknown parameter" message: the
    caller knows whether `params` came from the curated menu (the default) or
    was supplied by the agent (`source="supplied"`), and the two audiences need
    different advice. A curated-menu drift is a NExtSEEK bug the agent cannot
    fix, so it's told to correct the curated file. A supplied param is one the
    agent chose and CAN fix, so it's told to drop or correct the value it sent
    — telling it to "correct the curated file" would send it to edit a file it
    has no tool for and no business touching.

    `required=True` also reports required params `params` leaves unset (missing_required). It
    is off by default because only a caller holding the run's WHOLE param set may ask for it:
    on a part of the run (configure_run checks it split by who supplied each param) every
    required param the other part sets would be a false error.
    """
    props, skip = _load_properties(pipeline_key, revision, schema_getter)
    if props is None:
        return [], skip

    errors: list[str] = []
    for name, value in (params or {}).items():
        prop = props.get(name)
        if prop is None:
            if source == "supplied":
                advice = "drop it or correct the value you sent"
            else:
                advice = "the curated menu has drifted from the pipeline; drop it or correct the curated file"
            errors.append(
                f"param {name!r} is not a parameter of nf-core/{pipeline_key}@{revision} — "
                f"{advice}. nf-schema aborts the run on an unknown parameter.")
            continue
        if value is None:
            continue
        allowed = prop.get("enum")
        if allowed and value not in allowed:
            errors.append(
                f"param {name!r} value {value!r} is not allowed by "
                f"nf-core/{pipeline_key}@{revision} (choose from {allowed}).")
            continue
        # Handle both string and list-valued type declarations; drop "null" since
        # None values are already skipped and "null" adds no information.
        original_type = prop.get("type")
        declared = original_type
        if isinstance(declared, str):
            declared = [declared]
        types = [t for t in declared if t != "null"] if isinstance(declared, list) else []
        # All declared types must be recognisable, or we cannot judge and must not guess.
        # Judging on a partial set could reject a value that is legal under an
        # unrecognised type — a false rejection, which is never acceptable.
        if types and all(isinstance(t, str) and t in _TYPE_CHECKS for t in types):
            if not any(_TYPE_CHECKS[t](value) for t in types):
                type_str = original_type if isinstance(original_type, str) else repr(original_type)
                errors.append(
                    f"param {name!r} should be {type_str} per "
                    f"nf-core/{pipeline_key}@{revision}, got {type(value).__name__} {value!r}.")
    if required:
        unset, _ = missing_required(pipeline_key, revision, params, schema_getter=schema_getter)
        by_genome = "genome" in props
        errors += [required_message(pipeline_key, revision, name,
                                    by_genome=by_genome and name in GENOME_FILLS)
                   for name in unset]
    return errors, None


def check_reference_flags(pipeline_key: str, revision: str | None,
                          *, schema_getter=None) -> list[str]:
    """Report catalog-declared reference flags the pinned schema does not declare.

    `reference_cli_flags` is hand-maintained and says which of genome/fasta/gtf
    the pipeline accepts; the submitter injects from that list. A flag the
    schema does not declare aborts the run at nf-schema. This is advisory rather
    than an error because a declared flag is not necessarily passed on any given
    run — blocking on an unused declaration would break working builds.
    """
    declared = list((NFCORE_PIPELINE_CATALOG.get(pipeline_key) or {}).get("reference_cli_flags") or [])
    if not declared:
        return []
    props, skip = _load_properties(pipeline_key, revision, schema_getter)
    if props is None:
        return []
    missing = [flag for flag in declared if flag not in props]
    if not missing:
        return []
    return [(f"the catalog declares reference flag(s) {', '.join(missing)} for "
             f"{pipeline_key}, which nf-core/{pipeline_key}@{revision} does not declare — "
             "passing one aborts the run at nf-schema.")]
