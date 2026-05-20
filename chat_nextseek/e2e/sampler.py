"""Variant sampler with per-family floor + env satisfaction."""
from __future__ import annotations

import os
import random

from e2e.catalog import Catalog, Variant


def env_satisfied(v: Variant) -> bool:
    """True if every var in v.requires_env is set in the environment."""
    return all(os.environ.get(name) for name in v.requires_env)


def sample(catalog: Catalog, ratio: float, rng: random.Random) -> list[Variant]:
    """Pick variants per family at the given ratio, with max(1, round(N*ratio)) floor.

    Variants whose requires_env aren't satisfied are filtered out before sampling.
    Families left with zero eligible variants are skipped entirely.
    """
    selected: list[Variant] = []
    for family_name, family in catalog.families.items():
        eligible = [v for v in family.variants if env_satisfied(v)]
        if not eligible:
            continue
        k = max(1, round(len(eligible) * ratio))
        k = min(k, len(eligible))
        selected.extend(rng.sample(eligible, k))
    return selected
