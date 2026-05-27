// ─── Constraint Enums ────────────────────────────────

enum Score {
  PASS @description("Criterion fully satisfied")
  PARTIAL @description("Criterion partially satisfied — some issues")
  FAIL @description("Criterion not satisfied — significant issues")
}

enum Verdict {
  PASS @description("All criteria acceptable — no retry needed")
  RETRY @description("Issues found that may be fixable with a better query")
  FAIL @description("Critical failure — result is not usable and retry unlikely to help")
}

enum SuggestionCategory {
  REFORMULATE_QUERY @description("Rephrase the query for clarity or precision")
  CHANGE_MODE @description("Use a different assistant mode (e.g. plan instead of standard)")
  ADD_CONTEXT @description("Add missing context like sample type, date range, or project")
  NARROW_SCOPE @description("Make the query more specific to reduce noise")
  BROADEN_SCOPE @description("Make the query broader if it returned too few results")
}

enum AssistantStatus {
  PENDING
  RUNNING
  COMPLETED
  ERROR
}

enum ExecutionMode {
  STANDARD
  PLAN
}

enum PathMode {
  NEW_SEARCH
  REFINE_LAST_SEARCH
  GRAPH_QUERY
  REPORTER
  SYSTEM_QUESTION
  ASK_ABOUT_LAST_RESULTS
  UNSUPPORTED
  PLAN
}

// ─── Input Types ─────────────────────────────────────

class EvalLookup {
  task_id string?
  session_id string?
  bundle_id int?
  source string @description("'task' or 'bundle'")
}

class EvalRunMeta {
  status AssistantStatus
  query string?
  reply string?
}

class EvalRouting {
  execution_mode ExecutionMode
  path_mode PathMode
  path_subtype string? @description("e.g. reporter.summary, plan.step.*")
}

class SearchSignals {
  api_ok bool?
  api_status_code int?
  rows_returned int?
}

class GraphSignals {
  graph_ok bool?
  rows_returned int?
}

class PlanSignals {
  steps_total int?
  steps_executed int?
  steps_ok int?
  steps_failed int?
  stop_reason string?
}

class EvalRetryContext {
  retryable bool
  query_error_present bool
  raw_error_excerpt string?
  bundle_present bool
  has_artifacts bool
  has_prior_context bool
  search_signals SearchSignals?
  graph_signals GraphSignals?
  plan_signals PlanSignals?
}

class EvaluatorInput {
  lookup EvalLookup
  run EvalRunMeta
  routing EvalRouting
  retry_context EvalRetryContext
}

// ─── Output Types ────────────────────────────────────

class EvaluationJudgment {
  correctness Score @description("Does the reply correctly answer the query given the data?")
  completeness Score @description("Did the assistant return sufficient data for the query?")
  routing_quality Score @description("Did the assistant choose the right mode and construct a valid request?")
  reasoning string @description("Brief explanation of the judgment")
}

class Suggestion {
  category SuggestionCategory
  description string @description("Specific actionable suggestion")
}

class RetryDecision {
  verdict Verdict @description("Overall quality assessment")
  should_retry bool @description("Whether to attempt a retry")
  retry_query string? @description("Improved query text if retrying")
  suggestions Suggestion[]? @description("Structured improvement suggestions when retrying")
  reasoning string @description("Why this verdict and retry decision")
}
