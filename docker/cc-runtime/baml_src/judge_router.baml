// Phase 7 Residual #5 — semantic answer-quality judge for the router E2E harness.
//
// `tools/e2e/run_router_e2e.py` invokes `b.JudgeRouterAnswer(input=...)` after
// each query to determine whether the agent's reply is semantically satisfactory.
// The harness exit code is now gated on BOTH route-match AND verdict == PASS.
//
// Distinct from `baml_src/judge_ui.baml`, which serves a different
// (UI walkthrough) plan with a richer verdict enum (passed/failed/refused/
// unsupported/fabricated/error). Here we use a coarser PASS/FAIL/INCONCLUSIVE
// scheme appropriate for a 5-query route-discrimination harness.

class RouterJudgeInput {
  query_id         string
  query_text       string
  expected_route   string
  actual_route     string
  reply_text       string
  frames_summary   string
}

enum RouterJudgeVerdict {
  Pass         @alias("PASS")
  Fail         @alias("FAIL")
  Inconclusive @alias("INCONCLUSIVE")
}

class RouterJudgeOutput {
  verdict   RouterJudgeVerdict
  reasoning string
}

function JudgeRouterAnswer(input: RouterJudgeInput) -> RouterJudgeOutput {
  client GCPReasoner
  prompt #"
    You evaluate whether a research-assistant's reply to a user query is
    semantically satisfactory. The reply was produced by one of three
    execution routes: `nextseek_query` (a deterministic NExtSEEK metadata
    pipeline), `container_cc` (a generic Claude Code agent inside a
    container), or `unrelated` (the query is outside the assistant's scope
    and is answered with a fixed out-of-scope message, with no agent run).
    Output a single structured verdict.

    Verdict semantics:
    - PASS: the reply visibly addresses the query. For an `nextseek_query`
      route, the reply contains a non-trivial structured answer (sample
      counts, project metadata, lineage, etc.). For a `container_cc` route,
      the reply contains a substantive on-topic answer. For an `unrelated`
      route, a brief polite message that declines the out-of-scope request
      and explains the assistant only handles NExtSEEK / BioMicro Center lab
      and research-data tasks IS correct (PASS). For a query the system
      genuinely cannot support, an explicit refusal that names the
      limitation and (optionally) redirects the user is also PASS.
    - FAIL: the reply is empty, an error stub, off-topic, evasive, or
      asserts inability without explanation. A bare "I can't turn that into
      a valid NExtSEEK operation" with no further context is FAIL.
    - INCONCLUSIVE: only when you genuinely cannot tell from the reply
      whether it addresses the query (e.g. the reply is in a language you
      cannot read, or the query is so ambiguous no answer is judgeable).

    Keep `reasoning` to 1-3 sentences. Cite the most load-bearing phrase
    from the reply when possible.

    **Query ID:** {{ input.query_id }}
    **User query:** {{ input.query_text }}
    **Expected route:** {{ input.expected_route }}
    **Actual route:** {{ input.actual_route }}
    **Frames summary (one line per frame type):** {{ input.frames_summary }}
    **Agent reply text:**
    {{ input.reply_text }}

    {{ ctx.output_format }}
  "#
}
