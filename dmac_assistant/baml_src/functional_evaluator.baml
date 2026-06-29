// T0.1 — Stage C BAML schema for functional usefulness evaluator.
//
// Locked-spec anchors:
//   - DD-16: enum value sets (verbatim)
//   - DD-22: 7 non-negotiable prompt rules (verbatim in function's prompt block)
//   - DD-31: ExpectedBehavior / ArtifactKind / PrimaryIssue marked @@dynamic
//            (runtime extension via baml-py TypeBuilder is T2.1's responsibility)
//   - DD-32: Stage C wires to gemini-3.1-pro-preview via the google-ai provider.
//            Per AM-002 (drift D-6, /ultraplan followup 2026-05-21) Stage C REUSES
//            the existing `GCPReasoner` LLM client declared in `baml_src/clients.baml`
//            — it declares NO new client. The `cc2c43b` BAML consolidation made
//            GCPReasoner the single canonical client (gemini-3.1-pro-preview, google-ai,
//            retry_policy Exponential, api_key env.GCP_API_KEY) for ALL DMAC BAML
//            functions; BAML's single-`baml_src/` namespace makes it visible here.
//   - §7: BAML class shape (FunctionalEvaluationInput, FunctionalEvaluation)
//
// DL-017: PrimaryIssue.NoIssue replaces original `None` to avoid Python-keyword collision.
//
// NOTE: this file declares NO LLM-client block and NO `retry_policy` block. Both the
// `GCPReasoner` client and `retry_policy Exponential` are already declared in
// `baml_src/clients.baml` and live in BAML's global top-level namespace, visible to
// every file in `baml_src/`. The `EvaluateFunctionalUsefulness` function below
// references `client GCPReasoner` directly. Re-declaring either block here would cause
// BAML codegen to fail with `DuplicateTopLevel`.

// FailureMode — re-declared here in BAML wire form so the BAML function can type-check;
// the Python side reuses `tools.hibayes.exporter.FailureMode` (locked DD-16 reuse rule).
//
// BAML grammar requires enum value identifiers to start with an uppercase letter
// (see `baml_src/judge_ui.baml` `JudgeVerdict` enum — same constraint applies). We use
// `@alias("<lowercase>")` to expose the lowercase wire form that matches the Python
// `FailureMode` enum values at `tools/hibayes/exporter.py:147-151` verbatim.
// `None_` carries a trailing underscore because `None` is a BAML/Python reserved-token
// hazard (parallel to `PrimaryIssue.NoIssue` per DL-017); the `@alias("none")` keeps
// the wire string identical to `FailureMode.none.value`.
enum FailureMode {
  None_ @alias("none")
  Timeout @alias("timeout")
  Error @alias("error")
  NoAnswer @alias("no_answer")
}

// ExpectedBehavior — locked DD-16; @@dynamic per DD-31.
enum ExpectedBehavior {
  AnswerDirectly
  GenerateArtifact
  ClarifyIfAmbiguous
  UsePriorContext
  StateUnsupportedBoundary
  RefuseUnsafeOnly
  @@dynamic
}

// ArtifactStatus — locked DD-16 (10 values incl. PartialAfterFailure per DD-36).
enum ArtifactStatus {
  Valid
  Missing
  Inaccessible
  Unreadable
  SchemaInvalid
  Incomplete
  RuntimeFailed
  PartialAfterFailure
  Indeterminate
  NotExpected
}

// ArtifactKind — locked DD-16; @@dynamic per DD-31.
enum ArtifactKind {
  GEO_XLSX
  SRA_PACKAGE
  PRIDE_PACKAGE
  NFCORE_RNASEQ_CSV
  NFCORE_SCRNASEQ_CSV
  SVG_CHART
  UNKNOWN_FILE
  NONE_EXPECTED
  @@dynamic
}

// FunctionalOutcome — locked DD-16.
enum FunctionalOutcome {
  FullySatisfied
  PartiallySatisfied
  AppropriateClarification
  AppropriateBoundary
  NotSatisfied
  NotAssessable
}

// PrimaryIssue — locked DD-16 + DL-017; @@dynamic per DD-31.
// `NoIssue` replaces original `None` to avoid Python-keyword collision.
enum PrimaryIssue {
  NoIssue
  RuntimeFailure
  Timeout
  MissingArtifact
  InvalidArtifact
  IncompleteArtifact
  MissingContext
  AmbiguousRequest
  OverBroadSearch
  UpstreamApiError
  UnsupportedRequest
  RefusalError
  OverclaimedSuccess
  InsufficientEvidence
  Other
  @@dynamic
}

// ReviewPriority — locked DD-16.
enum ReviewPriority {
  Low
  Medium
  High
}

class FunctionalEvaluationInput {
  task_family string
  query_text string
  final_answer string?
  answer_provided bool
  runtime_success bool
  failure_mode FailureMode
  expected_behavior ExpectedBehavior
  artifact_expected bool
  artifact_status ArtifactStatus?
  artifact_kind ArtifactKind?
  declared_artifact_count int
}

class FunctionalEvaluation {
  outcome FunctionalOutcome
  usefulness_score int @description("0 to 4. 0 = unusable, 4 = fully useful.")
  primary_issue PrimaryIssue
  needs_human_review bool
  review_priority ReviewPriority
  rationale string @description("One or two concise sentences.")
}

// Locked DD-22 — 7 non-negotiable prompt rules verbatim.
function EvaluateFunctionalUsefulness(input: FunctionalEvaluationInput) -> FunctionalEvaluation {
  client GCPReasoner
  prompt #"
    You are evaluating the functional usefulness of an answer produced by a
    NExtSEEK chat agent against a user query. Output a single structured judgment.

    Non-negotiable rules (do not violate any of them):
    1. Judge functional usefulness only — not database correctness (unless the
       answer itself reveals a clear data-correctness problem).
    2. Do not invent missing facts.
    3. Do not use runtime cost or latency as a signal — those are tracked elsewhere.
    4. Treat valid artifact generation as REQUIRED when expected_behavior == GenerateArtifact.
    5. If runtime_success == false, the response is usually NotSatisfied UNLESS
       expected_behavior was an appropriate boundary (e.g., StateUnsupportedBoundary,
       RefuseUnsafeOnly).
    6. If artifact_status is one of Incomplete, Missing, SchemaInvalid, RuntimeFailed,
       do NOT mark FullySatisfied.
    7. Clarification requests are judged on appropriateness for the user request.

    Input:
    - task_family: {{ input.task_family }}
    - query_text: {{ input.query_text }}
    - final_answer: {{ input.final_answer }}
    - answer_provided: {{ input.answer_provided }}
    - runtime_success: {{ input.runtime_success }}
    - failure_mode: {{ input.failure_mode }}
    - expected_behavior: {{ input.expected_behavior }}
    - artifact_expected: {{ input.artifact_expected }}
    - artifact_status: {{ input.artifact_status }}
    - artifact_kind: {{ input.artifact_kind }}
    - declared_artifact_count: {{ input.declared_artifact_count }}

    Keep rationale to 1–2 concise sentences. Choose outcome, usefulness_score
    (0–4), primary_issue, needs_human_review, and review_priority per the rules
    above and your judgment.

    {{ ctx.output_format }}
  "#
}
