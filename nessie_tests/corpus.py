from __future__ import annotations
import json
import random
from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.catalog import load_catalog, Catalog, Variant  # noqa: E402

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
    return out


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
