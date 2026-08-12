"""Evidence kind and schema-version constants for V4-7 separation."""
from __future__ import annotations

from enum import Enum

__all__ = [
    "EvidenceKind",
    "PAIRED_RUN_SCHEMA_VERSION",
    "ONLINE_OBSERVATION_SCHEMA_VERSION",
    "OnlineEvidenceRejected",
    "UnapprovedPairedRun",
    "MixedEvidenceBatch",
    "ForgedEvidenceDiscriminator",
]


class EvidenceKind(str, Enum):
    paired_experimental = "paired_experimental"
    online_observational = "online_observational"


PAIRED_RUN_SCHEMA_VERSION = "paired_run/v1"
ONLINE_OBSERVATION_SCHEMA_VERSION = "online_observation/v1"


class OnlineEvidenceRejected(ValueError):
    """Online or observational evidence cannot enter the paired fitter."""


class UnapprovedPairedRun(ValueError):
    """Paired run ID is not in the approved registry."""


class MixedEvidenceBatch(ValueError):
    """Batch mixes experimental and observational evidence kinds."""


class ForgedEvidenceDiscriminator(ValueError):
    """Evidence kind or schema version does not match declared type."""
