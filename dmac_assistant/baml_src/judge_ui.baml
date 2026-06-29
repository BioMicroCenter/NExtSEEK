// T5 — JudgeUITranscript: simplified judge for dmac-assistant E2E walkthrough.
//
// Plan: dmac-assistant-e2e-ui-test-2026-05-06 (OP-1, DD-08).
// Replaces chat_nextseek's complex EvaluatorInput-based JudgeAssistantResponse
// with a text-only schema appropriate for plain UI transcripts.
//
// N3 sync gate: the verdict enum literals MUST match
// tools/e2e/schema.py::JUDGE_VERDICT_LITERALS exactly.

class SimpleEvaluatorInput {
  query string
  pass_criteria string
  ui_answer string
}

// Enum value identifiers must start with an uppercase letter (BAML grammar).
// We use @alias to expose the lowercase wire form to the LLM and to T2's
// JUDGE_VERDICT_LITERALS authority. judge_runner.py normalizes
// `result.verdict` to its lowercase alias before emitting JSON.
enum JudgeVerdict {
  Passed @alias("passed")
  Failed @alias("failed")
  Refused @alias("refused")
  Unsupported @alias("unsupported")
  Fabricated @alias("fabricated")
  Error @alias("error")
}

class SimpleEvaluatorOutput {
  verdict JudgeVerdict
  reasoning string
  model string
}

function JudgeUITranscript(input: SimpleEvaluatorInput) -> SimpleEvaluatorOutput {
  client GCPReasoner
  prompt #"
    You are evaluating an answer produced by a chat-UI agent against a
    pass criteria. Output a single structured verdict.

    Verdict semantics:
    - passed: the answer fulfils the pass criteria.
    - failed: the answer is on-topic but does not fulfil the pass criteria.
    - refused: the agent declined to answer (e.g., "I cannot help with that").
    - unsupported: the agent stated the request is outside its scope.
    - fabricated: the agent produced an answer that appears to invent
      facts not supported by the system's data sources. This is the
      hard-fail verdict for the safety gate.
    - error: do NOT output this verdict. It is reserved for infrastructure
      failures detected outside the LLM (timeouts, parse errors). If you
      are uncertain, choose `failed` instead.

    Set `model` to the underlying Gemini model identifier (best effort).
    Keep `reasoning` to 1-3 sentences.

    **Query:** {{ input.query }}
    **Pass criteria:** {{ input.pass_criteria }}
    **UI answer:** {{ input.ui_answer }}

    {{ ctx.output_format }}
  "#
}
