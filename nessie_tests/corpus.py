from __future__ import annotations
import json
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
    ov = load_overlay(overlay_path) if overlay_path else []
    return load_base() + ov


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
