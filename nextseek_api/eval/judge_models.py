"""Pydantic mirrors of BAML functional evaluator schemas (ported from dmac-assistant@dcca50c)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArtifactKind",
    "ArtifactStatus",
    "ExpectedBehavior",
    "FailureMode",
    "FunctionalEvaluation",
    "FunctionalEvaluationInput",
    "FunctionalOutcome",
    "PrimaryIssue",
    "ReviewPriority",
]


class FailureMode(str, Enum):
    none = "none"
    timeout = "timeout"
    error = "error"
    no_answer = "no_answer"


class ExpectedBehavior(str, Enum):
    AnswerDirectly = "AnswerDirectly"
    GenerateArtifact = "GenerateArtifact"
    ClarifyIfAmbiguous = "ClarifyIfAmbiguous"
    UsePriorContext = "UsePriorContext"
    StateUnsupportedBoundary = "StateUnsupportedBoundary"
    RefuseUnsafeOnly = "RefuseUnsafeOnly"


class ArtifactStatus(str, Enum):
    Valid = "Valid"
    Missing = "Missing"
    Inaccessible = "Inaccessible"
    Unreadable = "Unreadable"
    SchemaInvalid = "SchemaInvalid"
    Incomplete = "Incomplete"
    RuntimeFailed = "RuntimeFailed"
    PartialAfterFailure = "PartialAfterFailure"
    Indeterminate = "Indeterminate"
    NotExpected = "NotExpected"


class ArtifactKind(str, Enum):
    GEO_XLSX = "GEO_XLSX"
    SRA_PACKAGE = "SRA_PACKAGE"
    PRIDE_PACKAGE = "PRIDE_PACKAGE"
    NFCORE_RNASEQ_CSV = "NFCORE_RNASEQ_CSV"
    NFCORE_SCRNASEQ_CSV = "NFCORE_SCRNASEQ_CSV"
    SVG_CHART = "SVG_CHART"
    UNKNOWN_FILE = "UNKNOWN_FILE"
    NONE_EXPECTED = "NONE_EXPECTED"


class FunctionalOutcome(str, Enum):
    FullySatisfied = "FullySatisfied"
    PartiallySatisfied = "PartiallySatisfied"
    AppropriateClarification = "AppropriateClarification"
    AppropriateBoundary = "AppropriateBoundary"
    NotSatisfied = "NotSatisfied"
    NotAssessable = "NotAssessable"


class PrimaryIssue(str, Enum):
    NoIssue = "NoIssue"
    RuntimeFailure = "RuntimeFailure"
    Timeout = "Timeout"
    MissingArtifact = "MissingArtifact"
    InvalidArtifact = "InvalidArtifact"
    IncompleteArtifact = "IncompleteArtifact"
    MissingContext = "MissingContext"
    AmbiguousRequest = "AmbiguousRequest"
    OverBroadSearch = "OverBroadSearch"
    UpstreamApiError = "UpstreamApiError"
    UnsupportedRequest = "UnsupportedRequest"
    RefusalError = "RefusalError"
    OverclaimedSuccess = "OverclaimedSuccess"
    InsufficientEvidence = "InsufficientEvidence"
    Other = "Other"


class ReviewPriority(str, Enum):
    Low = "Low"
    Medium = "Medium"
    High = "High"


class FunctionalEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_family: str
    query_text: str
    final_answer: str | None
    answer_provided: bool
    runtime_success: bool
    failure_mode: FailureMode
    expected_behavior: ExpectedBehavior
    artifact_expected: bool
    artifact_status: ArtifactStatus | None
    artifact_kind: ArtifactKind | None
    declared_artifact_count: int


class FunctionalEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: FunctionalOutcome
    usefulness_score: int = Field(ge=0, le=4)
    primary_issue: PrimaryIssue
    needs_human_review: bool
    review_priority: ReviewPriority
    rationale: str
