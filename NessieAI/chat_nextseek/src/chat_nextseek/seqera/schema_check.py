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
Nothing here reads a default from the schema or adds a parameter to a run.

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


def _load_properties(pipeline_key: str, revision: str | None,
                     schema_getter) -> tuple[dict[str, dict] | None, str | None]:
    """Return (properties, skip_reason). Exactly one is None."""
    if not revision:
        return None, (f"no pinned revision for {pipeline_key!r}, so its schema could "
                      "not be checked")
    get = schema_getter or get_schema
    try:
        documents = get(pipeline_key, revision)
    except Exception as exc:  # noqa: BLE001 - never block a build on a fetch
        return None, f"{pipeline_key}@{revision} schema unavailable ({exc})"
    props = schema_properties((documents or {}).get("nextflow_schema") or {})
    if not props:
        # An empty flatten means the document was not what we expected, not that
        # the pipeline has no parameters. Rejecting every param on that basis
        # would break every build for this pipeline.
        return None, (f"{pipeline_key}@{revision} schema declared no parameters, "
                      "so it was not used")
    return props, None


def check_params(pipeline_key: str, revision: str | None, params: dict[str, Any],
                 *, schema_getter=None) -> tuple[list[str], str | None]:
    """Check every final param against the pinned schema.

    Returns (errors, skip_reason). When skip_reason is set, errors is empty and
    the caller should validate on the curated menu alone.
    """
    props, skip = _load_properties(pipeline_key, revision, schema_getter)
    if props is None:
        return [], skip

    errors: list[str] = []
    for name, value in (params or {}).items():
        prop = props.get(name)
        if prop is None:
            errors.append(
                f"param {name!r} is not a parameter of nf-core/{pipeline_key}@{revision} — "
                "the curated menu has drifted from the pipeline; drop it or correct the "
                "curated file. nf-schema aborts the run on an unknown parameter.")
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
