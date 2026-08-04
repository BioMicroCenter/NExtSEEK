"""One-shot migration: catalog.json + overlay.json + retired.json -> corpus.json.

Kept after the migration rather than deleted, because `tests/test_catalog_drift.py`
re-runs its base-variant extraction to detect upstream changes nessie has not
adopted. It is a reference implementation, not dead code.

Ordering rules, which exist so the output is stable enough to diff:
  * families in the order catalog.json declares them, then overlay-only families
  * variants in catalog order within a family, then overlay-only variants
  * an overlay variant with a base id REPLACES the base one IN PLACE, keeping the
    base position (this mirrors `corpus.merged`, which keeps base ordering)
"""
from __future__ import annotations

import argparse
import json
import pathlib

from nessie_tests import corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_BLOCKS = ("criterion_rewrites", "route_policy", "family_floor", "consistency_groups")


def _variant_dict(v, *, origin: str, retirement: dict | None) -> dict:
    """One variant, definition first then metadata, so a diff reads naturally."""
    return {
        "id": v.id,
        "name": v.name,
        "tags": [t for t in v.tags if t not in ("base", "overlay")],
        "requires_env": list(v.requires_env),
        "turns": [
            {"label": t.label, "query": t.query,
             "pass_criteria": [c.model_dump(exclude_none=False) for c in t.pass_criteria]}
            for t in v.turns
        ],
        "status": "retired" if retirement else "active",
        "origin": origin,
        "is_bayesian": False,
        "hibayes_subtype": None,
        "expected_behavior": None,
        "artifact_expected": None,
        "artifact_kind": None,
        "retirement": retirement,
    }


def build(catalog_path, overlay_path, retired_path) -> dict:
    raw_catalog = json.loads(pathlib.Path(catalog_path).read_text(encoding="utf-8"))
    raw_overlay = json.loads(pathlib.Path(overlay_path).read_text(encoding="utf-8"))
    raw_retired = json.loads(pathlib.Path(retired_path).read_text(encoding="utf-8"))
    retirements = raw_retired.get("retired") or {}

    base = {v.id: v for v in corpus.load_base()}
    overlay = {v.id: v for v in corpus.load_overlay(pathlib.Path(overlay_path))}

    families: dict[str, dict] = {}

    def _emit(fam_name: str, description: str, variant, origin: str) -> None:
        fam = families.setdefault(fam_name, {"description": description, "variants": []})
        fam["variants"].append(
            _variant_dict(variant, origin=origin, retirement=retirements.get(variant.id))
        )

    # Base families, in catalog order. An overlay variant with a matching id takes
    # the base one's place so ordering matches `merged()`.
    for fam_name, fam in raw_catalog["families"].items():
        for raw_v in fam["variants"]:
            vid = raw_v["id"]
            if vid in overlay:
                _emit(fam_name, fam.get("description", ""), overlay[vid], "overlay")
            else:
                _emit(fam_name, fam.get("description", ""), base[vid], "base")

    # Overlay-only variants, appended in overlay order.
    for fam_name, fam in raw_overlay["families"].items():
        for raw_v in fam["variants"]:
            vid = raw_v["id"]
            if vid not in base:
                _emit(fam_name, fam.get("description", ""), overlay[vid], "overlay")

    out = {
        "version": 2,
        "_note": (
            "The single source of truth for nessie_tests. Adopted from "
            "chat_nextseek/e2e/catalog.json, which is NOT edited and still serves its "
            "own ten readers. Retirement is a `status` flip, not a deletion."
        ),
        "provenance": {
            "adopted_from": "chat_nextseek/e2e/catalog.json",
            "catalog_sha256": corpus.sha256_of(catalog_path),
            "adopted_on": "2026-08-04",
        },
        "families": families,
    }
    for block in POLICY_BLOCKS:
        out[block] = raw_overlay.get(block, {} if block != "consistency_groups" else [])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nessie_tests.scripts.build_corpus")
    ap.add_argument("--catalog", type=pathlib.Path, default=corpus._BASE_CATALOG)
    ap.add_argument("--overlay", type=pathlib.Path, default=ROOT / "overlay.json")
    ap.add_argument("--retired", type=pathlib.Path, default=ROOT / "retired.json")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "corpus.json")
    a = ap.parse_args(argv)
    payload = build(a.catalog, a.overlay, a.retired)
    a.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(f["variants"]) for f in payload["families"].values())
    print(f"{a.out}: {len(payload['families'])} families, {n} variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
