from __future__ import annotations
import json
import random
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
