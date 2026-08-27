"""Closed, source-bound deployment identity for Plan 018 V4-9.

The record deliberately has no ``contract`` phase.  V4-9 supports expand and
forward-migrate compatibility only; schema contraction and destructive rollback
are outside the allowed state space.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "DataIdentity",
    "DeployRecord",
    "GenerationIdentity",
    "GitIdentity",
    "RuntimeIdentity",
    "SchemaIdentity",
    "deploy_record_schema",
]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
NonEmpty = Annotated[str, Field(min_length=1)]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class GitIdentity(_ClosedModel):
    source_sha: GitSha
    diff_sha256: Sha256


class SchemaIdentity(_ClosedModel):
    generation: Annotated[int, Field(ge=1)]
    migration_leaf: NonEmpty
    migrations: tuple[NonEmpty, ...]
    fingerprint: Sha256

    @field_validator("migrations")
    @classmethod
    def migrations_are_unique_and_include_leaf(cls, value: tuple[str, ...], info):
        if not value or len(value) != len(set(value)):
            raise ValueError("migration set must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def leaf_is_in_set(self) -> "SchemaIdentity":
        if self.migration_leaf not in self.migrations:
            raise ValueError("migration leaf must appear in migration set")
        return self


class GenerationIdentity(_ClosedModel):
    active: Sha256
    prior: Sha256

    @model_validator(mode="after")
    def generations_are_distinct(self) -> "GenerationIdentity":
        if self.active == self.prior:
            raise ValueError("active and prior generations must be distinct")
        return self


class DataIdentity(_ClosedModel):
    database_sha256: Sha256
    artifact_sha256: Sha256
    tombstone_sha256: Sha256
    row_counts: dict[
        Literal[
            "judgments",
            "exclusions",
            "pending_attempts",
            "failed_attempts",
            "reservations",
            "tombstones",
        ],
        Annotated[int, Field(ge=1)],
    ]

    @field_validator("row_counts")
    @classmethod
    def every_required_seed_is_non_empty(cls, value: dict[str, int]) -> dict[str, int]:
        required = {
            "judgments",
            "exclusions",
            "pending_attempts",
            "failed_attempts",
            "reservations",
            "tombstones",
        }
        if set(value) != required or any(count < 1 for count in value.values()):
            raise ValueError("every required deployment seed must be non-empty")
        return value


class RuntimeIdentity(_ClosedModel):
    identity_id: NonEmpty
    release: Literal["old", "new"]
    role: Literal["web", "worker"]
    source_sha: GitSha
    image_digest: ImageDigest
    owner: NonEmpty
    min_schema_generation: Annotated[int, Field(ge=1)]
    max_schema_generation: Annotated[int, Field(ge=1)]
    queue_generation: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def schema_window_is_ordered(self) -> "RuntimeIdentity":
        if self.min_schema_generation > self.max_schema_generation:
            raise ValueError("runtime schema window is inverted")
        return self


class DeployRecord(_ClosedModel):
    schema_version: Literal["plan018-deploy-record/v1"]
    deploy_id: NonEmpty
    created_at: datetime
    owner: NonEmpty
    phase: Literal["expand", "migrate"]
    git: GitIdentity
    images: dict[Literal["prior", "candidate"], ImageDigest]
    database_schema: SchemaIdentity = Field(alias="schema")
    settings_sha256: Sha256
    schedule_state: dict[NonEmpty, bool]
    flag_state: dict[NonEmpty, bool]
    generations: GenerationIdentity
    data: DataIdentity
    network_identity: NonEmpty
    runtime_identities: tuple[RuntimeIdentity, ...]
    smoke_checks: dict[NonEmpty, bool]

    @field_validator("created_at")
    @classmethod
    def timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @field_validator("images")
    @classmethod
    def image_set_is_exact_and_immutable(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"prior", "candidate"}:
            raise ValueError("images must contain exact prior and candidate digests")
        if value["prior"] == value["candidate"]:
            raise ValueError("prior and candidate image digests must be distinct")
        return value

    @field_validator("schedule_state", "flag_state")
    @classmethod
    def state_maps_are_non_empty(cls, value: dict[str, bool]) -> dict[str, bool]:
        if not value:
            raise ValueError("schedule and flag state must be non-empty")
        return value

    @field_validator("smoke_checks")
    @classmethod
    def all_smokes_pass(cls, value: dict[str, bool]) -> dict[str, bool]:
        if not value or not all(value.values()):
            raise ValueError("every recorded smoke check must pass")
        return value

    @model_validator(mode="after")
    def runtime_matrix_is_complete_and_bound(self) -> "DeployRecord":
        identities = self.runtime_identities
        if len({item.identity_id for item in identities}) != len(identities):
            raise ValueError("runtime identity IDs must be unique")
        required = {
            ("old", "web"),
            ("new", "web"),
            ("old", "worker"),
            ("new", "worker"),
        }
        if {(item.release, item.role) for item in identities} != required:
            raise ValueError("deploy record requires old/new web and worker identities")
        for identity in identities:
            expected_digest = self.images[
                "prior" if identity.release == "old" else "candidate"
            ]
            if identity.image_digest != expected_digest:
                raise ValueError("runtime image digest is stale against deploy identity")
            if not (
                identity.min_schema_generation
                <= self.database_schema.generation
                <= identity.max_schema_generation
            ):
                raise ValueError("runtime identity is incompatible with schema generation")
            if identity.owner != self.owner:
                raise ValueError("runtime owner is stale against deploy identity")
        return self


def deploy_record_schema() -> dict:
    """Return the generated JSON Schema used by Task 7/8 evidence validators."""
    return DeployRecord.model_json_schema()
