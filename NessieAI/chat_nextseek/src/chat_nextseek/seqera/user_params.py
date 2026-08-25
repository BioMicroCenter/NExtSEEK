"""Required run parameters the user must supply, because nothing can derive them.

Some pipelines need a value that is neither in NExtSEEK's metadata nor inferable
from a FASTQ path — a CRISPR guide sequence, a Hi-C digestion protocol, a miRTrace
species code. Historically that made those pipelines "not config-only" and they
were rejected. They aren't: the launch wizard is already a multi-turn conversation
(``prompts/pipeline_agent.txt`` tells the agent to write plain text and stop when it
needs something), so the value can simply be asked for.

The contract is declared per pipeline in ``reports/templates/nfcore/<key>.json``
under ``required_user_params``, and ENFORCED here rather than in the prompt. A
prompt instruction can be forgotten mid-conversation; ``tool_configure_run``
calling ``missing_user_params`` cannot. Fail-closed is the point: submitting a
CRISPR run with the wrong guide does not error, it silently reports wrong editing
efficiency, so a missing value must block the launch rather than default.

One spec entry::

    {"name": "protospacer",
     "definition": "The ~20 nt guide RNA target sequence, WITHOUT the PAM.",
     "example": "GGCACTGCGGCTGGAGGTGG",
     "pattern": "^[ACTGNactgn]+$",        # optional; regex the value must match
     "allowed": ["targeted", "screening"], # optional; enum the value must be in
     "required_when": {"analysis": "targeted"},  # optional; only required if this matches
     "scope": "run"}                       # "run" (one answer for the cohort) or "sample"

``scope: "sample"`` is declared for honesty, not convenience: a per-sample value
should come from an uploaded sheet or curated metadata, not from typing N sequences
into chat. ``render_elicitation`` says so when it meets one.
"""
from __future__ import annotations

import re
from typing import Any

# The full curated JSON, not load_pipeline_context() — that one deliberately projects
# to {params, reference_resources}. Same intra-package accessor process_args_for uses.
from .pipeline_params import _load_pipeline_doc


def required_user_params(pipeline_key: str) -> list[dict[str, Any]]:
    """The declared ``required_user_params`` for a pipeline, or []."""
    doc = _load_pipeline_doc(pipeline_key) or {}
    specs = doc.get("required_user_params")
    return [s for s in specs if isinstance(s, dict) and s.get("name")] if isinstance(specs, list) else []


_TRUEISH = {"true", "1", "yes"}


def _matches(got: Any, want: Any) -> bool:
    """Loose equality across the JSON/Python boolean boundary.

    A template can only express ``true``; the agent may hand us Python ``True``
    or the string ``"true"``. Comparing with ``str()`` alone would make
    ``True != "true"`` and silently mis-gate.
    """
    if isinstance(want, bool) or str(want).lower() in _TRUEISH | {"false", "0", "no"}:
        def norm(v):
            return str(v).strip().lower() in _TRUEISH if not isinstance(v, bool) else v
        return norm(got) == norm(want)
    return str(got) == str(want)


def _is_active(spec: dict, params: dict) -> bool:
    """Is this spec in force, given what the user has already answered?

    ``required_when`` gates a param on another one's value — crisprseq's
    ``library`` matters only for a screening analysis, not a targeted one.
    Absent gate => always required. An unanswered gate => not yet required,
    so the wizard asks for the gate first rather than demanding both at once.

    ``required_unless`` is the inverse, and is needed where a param is escaped by
    a DIFFERENT param rather than selected by one: hic's ``digestion`` is required
    unless the library is DNase Hi-C, which is its own boolean flag and not a
    member of the digestion enum. Expressing that as ``required_when`` would make
    an unanswered flag read as "not required", which is backwards.
    """
    gate = spec.get("required_when")
    if isinstance(gate, dict):
        for key, want in gate.items():
            got = params.get(key)
            if got is None or not _matches(got, want):
                return False
    escape = spec.get("required_unless")
    if isinstance(escape, dict):
        for key, want in escape.items():
            if key in params and _matches(params.get(key), want):
                return False
    return True


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_user_params(pipeline_key: str, params: dict | None) -> list[dict[str, Any]]:
    """Specs that are in force but unanswered."""
    params = params or {}
    return [s for s in required_user_params(pipeline_key)
            if _is_active(s, params) and _blank(params.get(s["name"]))]


def validate_user_params(pipeline_key: str, params: dict | None) -> list[str]:
    """Human-readable problems with the values that WERE supplied.

    Catches the paste-level mistakes before a run burns cluster time: whitespace in
    a sequence, an RNA ``U`` where DNA is wanted, a PAM left on the end (caught by
    length rules the pipeline itself applies, not here), a typo'd enum.
    """
    params = params or {}
    errors: list[str] = []
    for spec in required_user_params(pipeline_key):
        name = spec["name"]
        value = params.get(name)
        if _blank(value):
            continue
        text = str(value).strip()
        allowed = spec.get("allowed")
        if isinstance(allowed, list) and allowed and text not in allowed:
            errors.append(f"{name}: {text!r} is not one of {allowed}")
            continue
        pattern = spec.get("pattern")
        if isinstance(pattern, str) and pattern:
            try:
                ok = re.fullmatch(pattern, text) is not None
            except re.error:                       # a bad pattern in the template
                continue                            # is a template bug, not user error
            if not ok:
                hint = spec.get("pattern_hint") or f"must match {pattern}"
                errors.append(f"{name}: {text!r} is invalid — {hint}")
    return errors


def render_elicitation(specs: list[dict[str, Any]]) -> str:
    """The question to put to the user: what, why, and a concrete example.

    Written as text the agent can relay more or less verbatim, because the whole
    point is that the user sees a definition and an example rather than a bare
    parameter name.
    """
    if not specs:
        return ""
    lines = ["Before this can run I need "
             f"{'a value' if len(specs) == 1 else 'some values'} "
             "that NExtSEEK does not record and I cannot infer from the data:", ""]
    for spec in specs:
        lines.append(f"- **{spec['name']}** — {spec.get('definition', '').strip()}")
        allowed = spec.get("allowed")
        if isinstance(allowed, list) and allowed:
            lines.append(f"  - one of: {', '.join(str(a) for a in allowed)}")
        if spec.get("example"):
            lines.append(f"  - example: `{spec['example']}`")
        if spec.get("scope") == "sample":
            lines.append("  - this varies PER SAMPLE. If the values differ across the "
                         "cohort, upload a sheet instead of typing them — and consider "
                         "curating them onto the sample type so they are reusable.")
    lines += ["", "I will not guess any of these: a wrong value here usually produces a "
                  "plausible-looking wrong answer rather than an error."]
    return "\n".join(lines)
