"""Load and validate the curated nf-core parameter atlas.

The parameter analog of ``seqera/nfcore_atlas.py``. It holds the judgment no
machine-readable schema carries: which metadata signal sets a data-driven
parameter, what independent evidence corroborates it, and what to ask when they
conflict or are absent. Structural problems raise; drift only warns.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.pipeline_params import _load_pipeline_doc, load_pipeline_context

ATLAS_PATH = Path(__file__).resolve().parent.parent / "context" / "nfcore_param_atlas.json"

_VALID_TARGETS = {"row_column", "run_param"}


class ParamAtlasError(Exception):
    """The parameter atlas file is structurally invalid."""


def load_param_atlas(path: str | Path | None = None) -> dict[str, Any]:
    """Read, validate, and return the parameter atlas.

    Raises ParamAtlasError on a missing 'pipelines' key, or a param spec missing
    'target'/'allowed'/'derive_from.signal' or carrying an unknown target.
    Warns (does not raise) when an entry names a pipeline absent from the catalog.
    """
    target = Path(path) if path is not None else ATLAS_PATH
    try:
        payload = json.loads(target.read_text())
    except FileNotFoundError as exc:
        raise ParamAtlasError(f"Parameter atlas not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise ParamAtlasError(f"Parameter atlas is not valid JSON: {target}: {exc}") from exc

    if not isinstance(payload, dict) or "pipelines" not in payload:
        raise ParamAtlasError(f"Parameter atlas must be an object with a 'pipelines' key: {target}")
    pipelines = payload["pipelines"]
    if not isinstance(pipelines, dict) or not pipelines:
        raise ParamAtlasError("Parameter atlas 'pipelines' must be a non-empty object")

    for key, entry in pipelines.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("params"), dict):
            raise ParamAtlasError(f"Atlas entry {key!r} must have a 'params' object")
        for pname, spec in entry["params"].items():
            where = f"{key}.{pname}"
            if not isinstance(spec, dict):
                raise ParamAtlasError(f"Param spec {where} is not an object")
            if spec.get("target") not in _VALID_TARGETS:
                raise ParamAtlasError(
                    f"Param spec {where} has target {spec.get('target')!r}; "
                    f"must be one of {sorted(_VALID_TARGETS)}")
            if not (isinstance(spec.get("allowed"), list) and spec["allowed"]):
                raise ParamAtlasError(f"Param spec {where} has no non-empty 'allowed' list")
            df = spec.get("derive_from")
            if not (isinstance(df, dict) and df.get("signal") and isinstance(df.get("map"), dict)):
                raise ParamAtlasError(f"Param spec {where} needs derive_from.signal and derive_from.map")
            if spec.get("on_absent") == "use_default" and spec.get("default") is None:
                raise ParamAtlasError(f"Param spec {where} sets on_absent=use_default but has no 'default'")

    for key in pipelines:
        if key not in NFCORE_PIPELINE_CATALOG:
            warnings.warn(f"parameter atlas names {key!r}, which is not in NFCORE_PIPELINE_CATALOG",
                          stacklevel=2)

    # Drift pass: a param spec's target should still name something real in the
    # pipeline's own curated docs. Skip pipelines already flagged above as unknown
    # to the catalog -- there's nothing curated to check them against.
    for key, entry in pipelines.items():
        if key not in NFCORE_PIPELINE_CATALOG:
            continue
        for pname, spec in entry["params"].items():
            where = f"{key}.{pname}"
            target = spec.get("target")
            if target == "run_param":
                menu = load_pipeline_context(key).get("params") or {}
                if pname not in menu:
                    warnings.warn(
                        f"parameter atlas {where} targets run_param, but {pname!r} is not in "
                        f"{key}'s curated params menu -- drifted from the menu",
                        stacklevel=2)
                elif spec.get("on_absent") == "use_default":
                    menu_default = (menu.get(pname) or {}).get("default")
                    if menu_default is not None and spec.get("default") != menu_default:
                        warnings.warn(
                            f"parameter atlas {where} has default {spec.get('default')!r}, but "
                            f"{key}'s curated params menu default for {pname!r} is {menu_default!r} "
                            "-- the safe default has drifted from the menu",
                            stacklevel=2)
            elif target == "row_column":
                pipeline_meta = _load_pipeline_doc(key).get("pipeline") or {}
                columns = set(pipeline_meta.get("required_columns") or []) | \
                    set(pipeline_meta.get("optional_columns") or [])
                if pname not in columns:
                    warnings.warn(
                        f"parameter atlas {where} targets row_column, but {pname!r} is not in "
                        f"{key}'s samplesheet columns -- drifted from the columns",
                        stacklevel=2)

    # Substring-collision pass: a marker for one allowed value must not be a
    # case-insensitive substring of a marker for a DIFFERENT allowed value (the
    # documented cDNA/DNA hazard -- "DNA" hides inside "cDNA").
    for key, entry in pipelines.items():
        for pname, spec in entry["params"].items():
            where = f"{key}.{pname}"
            allowed = spec.get("allowed") or []
            df = spec.get("derive_from") or {}
            _warn_marker_collisions(where, df.get("signal"), df.get("map") or {}, allowed)
            for corr in spec.get("corroborate_with") or []:
                marker_map = {a: corr.get(f"{a}_markers") for a in allowed}
                _warn_marker_collisions(where, corr.get("signal"), marker_map, allowed)

    return payload


def _warn_marker_collisions(where: str, signal: Any, marker_map: dict[str, list], allowed: list[str]) -> None:
    """Warn if any marker for one allowed value is a substring of a marker for another."""
    for a in allowed:
        for b in allowed:
            if a == b:
                continue
            for marker_a in marker_map.get(a) or []:
                for marker_b in marker_map.get(b) or []:
                    if str(marker_a).strip() and str(marker_a).lower() in str(marker_b).lower():
                        warnings.warn(
                            f"parameter atlas {where} signal {signal!r}: marker {marker_a!r} "
                            f"(for {a!r}) is a substring of marker {marker_b!r} (for {b!r}) -- "
                            "substring-collision hazard, a sample for one value will also "
                            "vote for the other",
                            stacklevel=3)


_CACHE: dict[str, Any] = {}


def _atlas(atlas: dict | None) -> dict:
    if atlas is not None:
        return atlas
    if "shipped" not in _CACHE:
        _CACHE["shipped"] = load_param_atlas()
    return _CACHE["shipped"]


def _params(pipeline_key: str, atlas: dict | None) -> dict[str, Any]:
    entry = (_atlas(atlas).get("pipelines") or {}).get(pipeline_key) or {}
    return entry.get("params") or {}


def data_driven_params(pipeline_key: str, atlas: dict | None = None) -> dict[str, Any]:
    """The param specs the atlas declares for this pipeline ({} if none)."""
    return dict(_params(pipeline_key, atlas))


def required_signals(pipeline_key: str, atlas: dict | None = None) -> set[str]:
    """Every metadata field the pipeline's specs reference (derive + corroborators)."""
    out: set[str] = set()
    for spec in _params(pipeline_key, atlas).values():
        out.add(spec["derive_from"]["signal"])
        for corr in spec.get("corroborate_with") or []:
            if corr.get("signal"):
                out.add(corr["signal"])
    return out


def _hits(text: Any, markers: list[str] | None) -> bool:
    """Case-insensitive substring: does any marker appear in text?"""
    t = str(text or "").lower()
    return any(str(m).lower() in t for m in (markers or []))


def _signal_vote(value: Any, allowed: list[str], marker_map: dict[str, list[str]]) -> str | list:
    """One signal's vote: an allowed value, "" for silent, or a list for ambiguous (>1)."""
    voted = [a for a in allowed if _hits(value, marker_map.get(a))]
    if not voted:
        return ""
    return voted[0] if len(voted) == 1 else voted


def evaluate_data_driven_param(spec: dict, signals: dict) -> dict[str, Any]:
    """Derive + corroborate one param from a leaf's signals; return {value, verdict, evidence}.

    verdict:
      corroborated            primary and >=1 corroborator agree on one value, none contradict
      derived_uncorroborated  exactly one value is voted, but not by primary+corroborator together
      conflict                two signals vote different values, or one signal is internally ambiguous
      absent                  no signal resolves any value
      defaulted               verdict would be absent, but the spec declares a safe default
    """
    allowed = spec["allowed"]
    df = spec["derive_from"]
    primary_sig = df["signal"]
    primary_vote = _signal_vote(signals.get(primary_sig), allowed, df["map"])

    votes: list[tuple[str, Any]] = [(primary_sig, primary_vote)]
    for corr in spec.get("corroborate_with") or []:
        marker_map = {a: corr.get(f"{a}_markers") for a in allowed}
        votes.append((corr["signal"], _signal_vote(signals.get(corr["signal"]), allowed, marker_map)))

    ambiguous = [s for s, v in votes if isinstance(v, list)]
    decided = {v for _, v in votes if isinstance(v, str) and v}
    voters = [(s, v) for s, v in votes if isinstance(v, str) and v]

    if not decided and not ambiguous:
        if spec.get("on_absent") == "use_default" and spec.get("default") is not None:
            return {"value": spec["default"], "verdict": "defaulted",
                    "evidence": f"no signal; used the safe default {spec['default']!r}"}
        return {"value": None, "verdict": "absent",
                "evidence": f"no {'/'.join(allowed)} signal in {', '.join(s for s, _ in votes)}"}
    if ambiguous or len(decided) > 1:
        ev = "; ".join(f"{s}={v}" for s, v in voters) or f"ambiguous in {', '.join(ambiguous)}"
        primary_val = primary_vote if isinstance(primary_vote, str) and primary_vote else None
        return {"value": primary_val, "verdict": "conflict", "evidence": ev}

    value = next(iter(decided))
    corroborated = (isinstance(primary_vote, str) and primary_vote == value
                    and any(s != primary_sig for s, _ in voters))
    verdict = "corroborated" if corroborated else "derived_uncorroborated"
    return {"value": value, "verdict": verdict,
            "evidence": "; ".join(f"{s}={v}" for s, v in voters)}


def evaluate_leaf(pipeline_key: str, signals: dict, atlas: dict | None = None) -> dict[str, dict]:
    """Evaluate every data-driven param the atlas declares for this pipeline."""
    return {name: evaluate_data_driven_param(spec, signals)
            for name, spec in _params(pipeline_key, atlas).items()}


_ROW_UID_KEYS = ("sample", "Sample")


def _leaf_verdict(evidence: dict, uid: str, param: str) -> dict | None:
    return ((evidence or {}).get(uid) or {}).get(param)


def check_row_column_params(pipeline_key: str, rows: list[dict], evidence: dict,
                            atlas: dict | None = None) -> dict[str, Any]:
    """Check each row's data-driven COLUMN value against its cached verdict.

    Returns ask_uids (conflict/absent -> must ask), ask_specs (the specs behind
    them), and corrections (uid, param, derived_value) where a decisive verdict
    disagrees with what the row carries.
    """
    ask_uids: list[str] = []
    ask_specs: dict[str, dict] = {}
    corrections: list[tuple[str, str, str]] = []
    for name, spec in _params(pipeline_key, atlas).items():
        if spec.get("target") != "row_column":
            continue
        for row in rows:
            uid = next((str(row[k]) for k in _ROW_UID_KEYS if row.get(k)), None)
            if uid is None:
                continue
            verdict = _leaf_verdict(evidence, uid, name)
            if not verdict:
                continue
            v = verdict.get("verdict")
            if v in ("conflict", "absent"):
                ask_uids.append(uid)
                ask_specs[name] = spec
            elif v in ("corroborated", "derived_uncorroborated", "defaulted"):
                if str(row.get(name)) != str(verdict.get("value")):
                    corrections.append((uid, name, verdict.get("value")))
    return {"ask_uids": sorted(set(ask_uids)), "ask_specs": ask_specs, "corrections": corrections}


def check_run_params(pipeline_key: str, params: dict, evidence: dict,
                     atlas: dict | None = None) -> dict[str, Any]:
    """Run-scope analog: a run_param whose leaves conflict/absent must be asked;
    if the supplied value disagrees with a unanimous decisive verdict, correct it."""
    ask_uids: list[str] = []
    ask_specs: dict[str, dict] = {}
    corrections: list[tuple[str, str, str]] = []
    for name, spec in _params(pipeline_key, atlas).items():
        if spec.get("target") != "run_param":
            continue
        per_leaf = {uid: (pv.get(name) or {}) for uid, pv in (evidence or {}).items()
                    if (pv or {}).get(name)}
        if not per_leaf:
            continue
        if any(v.get("verdict") in ("conflict", "absent") for v in per_leaf.values()):
            ask_uids += [u for u, v in per_leaf.items() if v.get("verdict") in ("conflict", "absent")]
            ask_specs[name] = spec
            continue
        values = {v.get("value") for v in per_leaf.values()}
        if len(values) > 1:
            # Every leaf individually decisive, but they disagree with each other —
            # a heterogeneous cohort for a run-scope param. Must ask, not silently
            # pass: there is no single value to write for the whole run.
            ask_uids += list(per_leaf)
            ask_specs[name] = spec
            continue
        if len(values) == 1 and (params or {}).get(name) is not None:
            derived = next(iter(values))
            if str(params.get(name)) != str(derived):
                corrections.append(("*", name, derived))
    return {"ask_uids": sorted(set(ask_uids)), "ask_specs": ask_specs, "corrections": corrections}


def render_param_elicitation(ask_specs: dict[str, dict], ask_uids: list[str], evidence: dict) -> str:
    """The plain-text question, in the render_elicitation house style."""
    if not ask_specs:
        return ""
    lines = ["Before this can run I need you to confirm a value I can't derive with confidence:", ""]
    for name, spec in ask_specs.items():
        ask = spec.get("ask") or {}
        lines.append(f"- **{name}** — {str(ask.get('definition', '')).strip()}")
        conflict = [u for u in ask_uids if (_leaf_verdict(evidence, u, name) or {}).get("verdict") == "conflict"]
        absent = [u for u in ask_uids if (_leaf_verdict(evidence, u, name) or {}).get("verdict") == "absent"]
        if conflict:
            lines.append(f"  - {str(ask.get('on_conflict', 'Signals disagree.')).strip()}")
            lines.append(f"    affected: {', '.join(sorted(conflict))}")
        if absent:
            lines.append(f"  - {str(ask.get('on_absent', 'No evidence.')).strip()}")
            lines.append(f"    affected: {', '.join(sorted(absent))}")
        disagreeing = [u for u in ask_uids
                       if u not in conflict and u not in absent
                       and (_leaf_verdict(evidence, u, name) or {}).get("verdict")
                       in ("corroborated", "derived_uncorroborated")]
        if disagreeing:
            lines.append("  - these samples disagree on the value:")
            lines.append(f"    affected: {', '.join(sorted(disagreeing))}")
        if spec.get("allowed"):
            lines.append(f"  - allowed: {', '.join(str(a) for a in spec['allowed'])}")
    lines += ["", "I will not guess: a wrong value here produces a plausible-looking wrong result rather than an error."]
    return "\n".join(lines)
