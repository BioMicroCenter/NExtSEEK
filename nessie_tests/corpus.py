from __future__ import annotations
import json
import random
import re
from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.catalog import load_catalog, Catalog, PassCriterion, Variant  # noqa: E402

_BASE_CATALOG = Path(__file__).resolve().parents[1] / "chat_nextseek" / "e2e" / "catalog.json"


def _flatten(cat: Catalog, source_tag: str) -> list[Variant]:
    out: list[Variant] = []
    for fam in cat.families.values():
        for v in fam.variants:
            if source_tag not in v.tags:
                v.tags = [*v.tags, source_tag]
            out.append(v)
    return out


def load_base() -> list[Variant]:
    return _flatten(load_catalog(_BASE_CATALOG), "base")


def load_overlay(path: Path) -> list[Variant]:
    return _flatten(load_catalog(path), "overlay")


def load_consistency_groups(path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("consistency_groups", [])


def load_family_floor(path) -> dict:
    """The per-family minimum OUTCOME assertion block from the overlay."""
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("family_floor", {})


def apply_family_floor(variants: list[Variant], floor_spec: dict) -> list[Variant]:
    """Add each family's minimum outcome assertion to the LAST turn of its variants.

    Of 381 merged variants, 108 assert plan shape only and 194 assert only plumbing
    (``api_ok`` / ``neo4j_ok`` say a request COMPLETED, not that it returned
    anything). 79% of the corpus therefore cannot detect a wrong answer, and
    hand-editing 300 variants is not viable — so the floor is applied structurally.

    Two rules keep it from doing harm:

    - a floor criterion is added only when the last turn does not already assert that
      FIELD, so a deliberate bound (``row_count lte 20000``) is never overwritten and
      never contradicted by an added one
    - a variant tagged ``no_floor`` opts out entirely. That is required wherever ZERO
      is the correct answer — ``advanced.find_me_nhp_samples_from_study`` (GBM does
      not exist), ``graph.what_pbmcs_tissues_in_the_gbm`` (the operator confirmed the
      zero is honest) and ``tree.missing_uid`` (which asserts a 404).

    Only families listed in the spec get a floor; unsupported, writes_unsupported,
    nessie_route, system_question and refine_and_recall are absent by design. Refine
    and recall turns have no ``api_result_meta.row_count`` at all — that field exists
    only on new_search turns — so a row-count floor there would fail every one of them.
    """
    floors = (floor_spec or {}).get("floors") or {}
    skip_tag = (floor_spec or {}).get("exclude_tag", "no_floor")
    if not floors:
        return variants

    for v in variants:
        floor = floors.get(v.family)
        if not floor or skip_tag in v.tags or not v.turns:
            continue
        last = v.turns[-1]
        already = {c.field for c in last.pass_criteria}
        for crit in floor:
            if crit["field"] in already:
                continue
            last.pass_criteria.append(PassCriterion(**crit))
    return variants


def load_criterion_rewrites(path) -> dict:
    """Structural corrections applied to every matching criterion in the corpus."""
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("criterion_rewrites", {})


def apply_criterion_rewrites(variants: list[Variant], spec: dict) -> list[Variant]:
    """Correct `reporter_plan.project eq "<Name>"` across the whole corpus.

    Two verified facts make every one of these 30 criteria false-fail:

    - the reporter UPPERCASES the project it resolved. Task 813 came back "METNET"
      for a plan asserting ``eq "MetNet"``.
    - for a LAB scope it returns ``project: null`` entirely. Task 812 shows
      ``reporter_plan.project: null`` with ``reporter_context.lab_codes: ["KAM"]``,
      so ``eq "Kamm"`` can never hold — Kamm is a lab, not a project.

    So a lab name is rewritten to assert the resolved lab code, and every other name
    becomes a case-insensitive regex. Doing it here rather than as 30 hand-written
    overlay overrides keeps the correction in one reviewable place.
    """
    labs = {k.upper(): v for k, v in (spec or {}).get("reporter_project_labs", {}).items()}
    if not spec or not spec.get("reporter_project_case_insensitive", False):
        return variants

    for v in variants:
        for t in v.turns:
            for i, c in enumerate(t.pass_criteria):
                if c.field != "reporter_plan.project" or c.op != "eq" or not isinstance(c.value, str):
                    continue
                code = labs.get(c.value.strip().upper())
                if code:
                    t.pass_criteria[i] = PassCriterion(
                        field="reporter_plan.reporter_context.lab_codes",
                        op="contains", value=code)
                else:
                    t.pass_criteria[i] = PassCriterion(
                        field="reporter_plan.project", op="matches_re",
                        value="(?i)" + re.escape(c.value.strip()))
    return variants


def load_route_policy(path) -> dict:
    """Which families are routed away from NS, and to where."""
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("route_policy", {})


def apply_route_policy(variants: list[Variant], spec: dict) -> list[Variant]:
    """Assert the ROUTE, not an NS-internal parser mode, wherever NS is not reached.

    23 variants across ``unsupported`` and ``writes_unsupported`` assert
    ``parser_plan.mode == "unsupported"``. When the router sends a turn to
    container_cc or unrelated, NS never runs and there is no parser plan, so the
    criterion resolves to None and fails unconditionally regardless of whether the
    product did the right thing. Three of the fifteen seed-0 failures were this.

    The policy those 23 encode is stale rather than wrong-in-detail: writes,
    exports and open-ended analysis are Container-CC's job. The overlay already
    blesses that in its route_gate family (route.cc_write_investigation,
    route.cc_open_ended_analysis) and a previous wave hand-converted three variants
    to exactly the shape this produces. This finishes the job in one place instead
    of 23 overlay overrides.

    Per-variant ``overrides`` exist because the family is not uniform: weather is
    ``unrelated``, and a textbook-chemistry question is defensibly either, so it is
    asserted as an alternation rather than pinning an undecided policy.
    """
    families = (spec or {}).get("families") or {}
    overrides = (spec or {}).get("overrides") or {}
    drop = (spec or {}).get("drop_field")
    if not families and not overrides:
        return variants

    for v in variants:
        rule = overrides.get(v.id) or families.get(v.family)
        if not rule or not v.turns:
            continue
        for t in v.turns:
            if drop:
                t.pass_criteria = [c for c in t.pass_criteria if c.field != drop]
        first = v.turns[0]
        if any(c.field == "route" for c in first.pass_criteria):
            continue  # already converted by hand; do not double-write
        first.pass_criteria.append(
            PassCriterion(field="route", op=rule["op"], value=rule["value"]))
    return variants


def merged(overlay_path: Path | None = None) -> list[Variant]:
    """Base catalog plus overlay, where an overlay variant may OVERRIDE a base one.

    An overlay variant whose ``id`` matches a base variant replaces it in place,
    keeping the base ordering and the base id. That is what lets us strengthen a
    weak imported expectation — e.g. a refine/recall case that only asserted
    ``parser_plan.mode``, and so passed while answering from the wrong result
    bundle — without editing the vendored ``chat_nextseek/e2e/catalog.json``.
    Overlay variants with a new id are appended as usual.
    """
    ov = load_overlay(overlay_path) if overlay_path else []
    by_id = {v.id: v for v in ov}
    base = load_base()
    out = [by_id.pop(v.id, v) for v in base]
    out += [v for v in ov if v.id in by_id]
    out = apply_criterion_rewrites(out, load_criterion_rewrites(overlay_path))
    out = apply_route_policy(out, load_route_policy(overlay_path))
    return apply_family_floor(out, load_family_floor(overlay_path))


def overridden_ids(overlay_path: Path | None = None) -> list[str]:
    """Ids where the overlay replaces a base variant (for reporting/debugging)."""
    if not overlay_path:
        return []
    base_ids = {v.id for v in load_base()}
    return sorted(v.id for v in load_overlay(overlay_path) if v.id in base_ids)


def select(variants, *, scope: str = "all", family: str | None = None,
           variant_id: str | None = None) -> list[Variant]:
    out = list(variants)
    if variant_id:
        return [v for v in out if v.id == variant_id]
    if family:
        out = [v for v in out if v.family == family]
    if scope == "specific":
        out = [v for v in out if "route_gate" in v.tags]
    return out


def sample(variants, ratio: float, seed: int = 0) -> list:
    """Deterministically keep a per-family fraction of the variants.

    ratio >= 1.0 returns everything. Otherwise each family keeps
    max(1, round(len(family) * ratio)) variants, chosen with a seeded RNG so
    the same (ratio, seed) always yields the same subset.
    """
    if ratio >= 1.0:
        return list(variants)
    rng = random.Random(seed)
    by_family: dict[str, list] = {}
    for v in variants:
        by_family.setdefault(v.family, []).append(v)
    out: list = []
    for fam in sorted(by_family):
        vs = by_family[fam]
        k = max(1, round(len(vs) * ratio))
        out.extend(rng.sample(vs, min(k, len(vs))))
    return out
