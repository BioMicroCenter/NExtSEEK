// ─── Evaluator Functions ─────────────────────────────

function JudgeAssistantResponse(input: EvaluatorInput) -> EvaluationJudgment {
  client ReasonerFallback
  prompt #"
    You are an evaluator for the NExtSEEK research assistant. Your job is to judge
    the quality of the assistant's response to a user query.

    ## Context

    The user submitted a query to the NExtSEEK assistant, which searches a biomedical
    sample management database. The assistant chose an execution path and produced a result.

    ## Input

    **Query:** {{ input.run.query }}
    **Reply:** {{ input.run.reply }}
    **Status:** {{ input.run.status }}
    **Execution Mode:** {{ input.routing.execution_mode }}
    **Path Mode:** {{ input.routing.path_mode }}
    {% if input.routing.path_subtype %}**Path Subtype:** {{ input.routing.path_subtype }}{% endif %}

    **Retry Signals:**
    - Bundle present: {{ input.retry_context.bundle_present }}
    - Error present: {{ input.retry_context.query_error_present }}
    {% if input.retry_context.raw_error_excerpt %}- Error: {{ input.retry_context.raw_error_excerpt }}{% endif %}
    {% if input.retry_context.search_signals %}- API OK: {{ input.retry_context.search_signals.api_ok }}, Status: {{ input.retry_context.search_signals.api_status_code }}, Rows: {{ input.retry_context.search_signals.rows_returned }}{% endif %}
    {% if input.retry_context.graph_signals %}- Graph OK: {{ input.retry_context.graph_signals.graph_ok }}, Rows: {{ input.retry_context.graph_signals.rows_returned }}{% endif %}
    {% if input.retry_context.plan_signals %}- Plan steps: {{ input.retry_context.plan_signals.steps_total }} total, {{ input.retry_context.plan_signals.steps_ok }} ok, {{ input.retry_context.plan_signals.steps_failed }} failed{% endif %}

    ## Evaluation Criteria

    1. **Correctness** (PASS/PARTIAL/FAIL): Does the reply answer the user's query? Is the information accurate based on the signals?
    2. **Completeness** (PASS/PARTIAL/FAIL): Did the assistant return sufficient data? Are artifacts present if expected?
    3. **Routing Quality** (PASS/PARTIAL/FAIL): Did the assistant choose the right execution path for this query?

    {{ ctx.output_format }}
  "#
}

function DecideRetry(input: EvaluatorInput, judgment: EvaluationJudgment) -> RetryDecision {
  client ReasonerFallback
  prompt #"
    You are the retry decision maker for the NExtSEEK evaluator. Based on the evaluation
    judgment, decide whether to retry the query.

    ## Original Query
    {{ input.run.query }}

    ## Evaluation Judgment
    - Correctness: {{ judgment.correctness }}
    - Completeness: {{ judgment.completeness }}
    - Routing Quality: {{ judgment.routing_quality }}
    - Reasoning: {{ judgment.reasoning }}

    ## Retry Context
    - Retryable: {{ input.retry_context.retryable }}
    - Path Mode: {{ input.routing.path_mode }}
    {% if input.retry_context.search_signals %}- Rows returned: {{ input.retry_context.search_signals.rows_returned }}{% endif %}

    ## Decision Rules

    - **PASS**: All scores are PASS. No retry needed.
    - **RETRY**: One or more scores are PARTIAL or FAIL, AND the issue is fixable with a
      better query. You MUST provide a retry_query and at least one Suggestion with a
      specific SuggestionCategory.
    - **FAIL**: Critical failure that cannot be fixed by retrying (e.g., the path mode is
      unsupported, or the assistant errored on infrastructure).

    If should_retry is true, you MUST:
    1. Set retry_query to an improved version of the original query
    2. Provide at least one Suggestion with a concrete category and description

    {{ ctx.output_format }}
  "#
}
