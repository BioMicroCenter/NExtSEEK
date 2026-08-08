"""HiBayes router input models — the runnable form of plan-018 V8-C and V8-D.

PROPOSAL, not an implementation. The plan's V8-C (the seventeen-field eval row) and V8-D
(the combined outcome and its total disposition mapping) specify this in prose; this file is
the same contract as executable pydantic v2, smoke-tested but not wired into anything.

Where this belongs in the integrated stack is NOT yet settled: the plan's File Structure table
is marked "Historical/incomplete under V4", and V4-0 requires a reviewed file/interface
ownership map before implementation. That map does not exist. The historical table points the
eval row at `nextseek_api/eval/export.py`; `StackVersion` and `error_class` (V8-E, V8-D) have
no assigned home anywhere in the plan.

Decisions locked with the maintainer 2026-08-07:
  Q1 stack version   -> stack_id on the row, StackVersion lookup table
  Q2 success         -> ONE combined bit: runtime AND artifact AND functional
  Q3 failure rule    -> per-mode total mapping, no default
  Q4 taxonomy        -> keep failure_mode, add error_class alongside; error_class carries disposition
  Q5 dispositions    -> code_error, timeout, no_answer all score 0
  Q6 promotion       -> promote every classified turn where family != "unrelated"; greedy, no dedup
  Q7 execution cache -> key (query_id, route, stack_id, task_family)

Derived from the working dmac_assistant pipeline at dcca50c.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Route is a Literal: the route set is closed and knowable at build time.
# task_family is NOT — it is corpus-owned and injected dynamically into the BAML
# enum (ClassifiedFamily, @@dynamic + TypeBuilder). Never a Literal, never hardcoded.
Route = Annotated[str, Field(pattern=r"^(nextseek_query|container_cc)$")]


class RouteSource(str, Enum):
    forced = "forced"        # imposed by the paired runner -> experimental evidence
    baml = "baml"            # chosen by the router -> observational
    sticky = "sticky"
    heuristic = "heuristic"


class FamilySource(str, Enum):
    corpus = "corpus"
    baml = "baml"


class FailureMode(str, Enum):
    """Coarse, precedence-resolved. Unchanged from tools/hibayes/exporter.py:147 so the
    existing exporter and prior runs stay readable. Precedence, highest first:
    timeout > error > no_answer > none. `no_answer` is the residual — not timed out,
    not an error, still no answer. It fired 0 times in the 103-row run."""

    none = "none"
    timeout = "timeout"
    error = "error"
    no_answer = "no_answer"


class ErrorClass(str, Enum):
    """Granular, and the field the disposition mapping actually reads. `usage_policy`
    is already emitted by the newer harness (arm_diagnostics.csv)."""

    none = "none"
    provider_outage = "provider_outage"  # transient, upstream down
    usage_policy = "usage_policy"        # container killed by policy trigger, canned message
    code_error = "code_error"            # real defect in our own code


class ArtifactStatus(str, Enum):
    not_expected = "not_expected"
    delivered_valid = "delivered_valid"
    delivered_invalid = "delivered_invalid"
    missing = "missing"


class Disposition(str, Enum):
    scored = "scored"      # enters n_total; success bit decides the numerator
    excluded = "excluded"  # enters neither n_total nor n_success


# Total mapping, no default. An unrecognised key fails closed (V8-D's final row, and V4-3's
# "unknown enum values ... are not coerced to success") rather
# than being coerced to success. Keys are checked in this order: error_class first,
# then failure_mode when error_class is `none`.
ERROR_CLASS_DISPOSITION: dict[ErrorClass, Disposition] = {
    ErrorClass.none: Disposition.scored,
    ErrorClass.provider_outage: Disposition.excluded,  # plan 592-593
    ErrorClass.usage_policy: Disposition.scored,       # genuine route incapability
    ErrorClass.code_error: Disposition.scored,
}

FAILURE_MODE_DISPOSITION: dict[FailureMode, Disposition] = {
    FailureMode.none: Disposition.scored,
    FailureMode.timeout: Disposition.scored,
    FailureMode.error: Disposition.scored,
    FailureMode.no_answer: Disposition.scored,
}


class StackVersion(BaseModel):
    """The four independently-versioned components. One record per distinct stack;
    rows reference it by stack_id so only one low-cardinality field enters the data
    and the fit's group key stays (task_family, route)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stack_id: str = Field(min_length=1)
    nextseek_image: str
    container_agent_image: str
    sidecar_image: str
    seek_image: str


class EvalRow(BaseModel):
    """One route arm of one question. Aggregated into RouteFamilyAggregate before fitting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- identity / pairing ------------------------------------------------
    query_id: str = Field(min_length=1)
    route: Route
    task_family: str = Field(min_length=1)  # never "unrelated" — those never reach the fit

    # --- provenance --------------------------------------------------------
    route_source: RouteSource
    family_source: FamilySource
    stack_id: str = Field(min_length=1)

    # --- deterministic runtime facts --------------------------------------
    answer_provided: bool
    is_error: bool
    timed_out: bool
    runtime_success: bool
    failure_mode: FailureMode
    error_class: ErrorClass = ErrorClass.none

    # --- deterministic cost facts -----------------------------------------
    latency_seconds: Annotated[float, Field(ge=0.0)]
    cost_usd: Annotated[float, Field(ge=0.0)] | None = None

    # --- deterministic artifact facts -------------------------------------
    artifact_expected: bool
    artifact_status: ArtifactStatus
    artifact_success: bool

    # --- the only judge-produced field ------------------------------------
    # Judged ONLY when both deterministic gates pass. Under AND semantics a row that
    # already fails runtime or artifact is 0 regardless, so it is never sent to the
    # judge — the combined bit is what makes that saving safe.
    functional_success: bool | None = None

    @model_validator(mode="after")
    def _runtime_success_is_derived(self) -> EvalRow:
        expected = self.answer_provided and not self.is_error and not self.timed_out
        if self.runtime_success is not expected:
            raise ValueError(
                f"runtime_success {self.runtime_success!r} inconsistent with flags; "
                f"expected {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def _artifact_success_is_derived(self) -> EvalRow:
        if self.artifact_status is ArtifactStatus.not_expected:
            if self.artifact_expected:
                raise ValueError("artifact_status=not_expected but artifact_expected=True")
            expected = True
        else:
            expected = self.artifact_status is ArtifactStatus.delivered_valid
        if self.artifact_success is not expected:
            raise ValueError(
                f"artifact_success {self.artifact_success!r} inconsistent with "
                f"artifact_status={self.artifact_status.value!r}"
            )
        return self

    @model_validator(mode="after")
    def _forced_arms_carry_corpus_labels(self) -> EvalRow:
        if self.route_source is RouteSource.forced and self.family_source is not FamilySource.corpus:
            raise ValueError(
                f"forced arms require family_source=corpus, got {self.family_source.value!r}"
            )
        return self

    @property
    def disposition(self) -> Disposition:
        """error_class decides; failure_mode is consulted only when error_class is none.
        Unrecognised values raise rather than defaulting to scored."""
        if self.error_class is not ErrorClass.none:
            try:
                return ERROR_CLASS_DISPOSITION[self.error_class]
            except KeyError:
                raise ValueError(f"no disposition for error_class={self.error_class!r}") from None
        try:
            return FAILURE_MODE_DISPOSITION[self.failure_mode]
        except KeyError:
            raise ValueError(f"no disposition for failure_mode={self.failure_mode!r}") from None

    def outcome(self) -> bool | None:
        """The single combined success bit. None means excluded — never coerced to failure.

        success = runtime_success AND artifact_success AND functional_success
        """
        if self.disposition is Disposition.excluded:
            return None
        if not (self.runtime_success and self.artifact_success):
            return False  # deterministic gate failed; no judge call was needed
        if self.functional_success is None:
            return None   # gates passed but unjudged -> excluded, not scored 0
        return self.functional_success


class RouteFamilyAggregate(BaseModel):
    """One binomial group — the literal fit input. two_level_group_binomial consumes
    n_total and obs=n_success per group, indexed by group_index."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_family: str = Field(min_length=1)
    route: Route

    n_total: Annotated[int, Field(ge=0)]    # -> features["n_total"]
    n_success: Annotated[int, Field(ge=0)]  # -> features["obs"]
    n_excluded: Annotated[int, Field(ge=0)] = 0

    avg_latency_seconds: Annotated[float, Field(ge=0.0)]
    avg_cost_usd: float | None = None

    @model_validator(mode="after")
    def _successes_cannot_exceed_trials(self) -> RouteFamilyAggregate:
        if self.n_success > self.n_total:
            raise ValueError(f"n_success {self.n_success} > n_total {self.n_total}")
        return self


def aggregate_by_family_and_route(rows: list[EvalRow]) -> list[RouteFamilyAggregate]:
    """Deterministic fold. Conservation holds per group: len(group) == n_total + n_excluded."""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[EvalRow]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for r in rows:
        key = (r.task_family, r.route)
        if key not in buckets:
            order.append(key)
        buckets[key].append(r)

    out: list[RouteFamilyAggregate] = []
    for family, route in order:
        group = buckets[(family, route)]
        scored = [(r, r.outcome()) for r in group]
        included = [(r, o) for r, o in scored if o is not None]
        costs = [r.cost_usd for r, _ in included if r.cost_usd is not None]
        out.append(
            RouteFamilyAggregate(
                task_family=family,
                route=route,
                n_total=len(included),
                n_success=sum(1 for _, o in included if o),
                n_excluded=len(group) - len(included),
                avg_latency_seconds=(
                    sum(r.latency_seconds for r, _ in included) / len(included)
                    if included else 0.0
                ),
                avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
            )
        )
    return out


def arm_cache_key(row: EvalRow) -> tuple[str, str, str, str]:
    """Execution-reuse key (Q7). A prior arm execution is reusable only when the
    question, route, stack and task family all match. Family is included because it
    selects family_floor / expected_behavior from the corpus's family_defaults, so a
    reclassified question is scored against different criteria — and the fit groups
    by family."""
    return (row.query_id, row.route, row.stack_id, row.task_family)
