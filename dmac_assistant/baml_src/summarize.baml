// Step 1c — host-side per-session distillation. Emitted as python/pydantic
// (output_type in generators.baml) so the bridge gets a validated pydantic
// SessionSummary natively. See NExtSEEK SPEC-1c §4.

enum MemoryCategory {
  Preference @alias("preference")
  Context    @alias("context")
  Artifact   @alias("artifact")
  Decision   @alias("decision")
  Todo       @alias("todo")
  Fact       @alias("fact")
}

enum Confidence {
  High   @alias("high")
  Medium @alias("medium")
  Low    @alias("low")
}

class EvidenceRef {
  line_start int     @assert(line_min, {{ this >= 1 }})
  line_end   int
  quote      string  @check(nonempty, {{ this|length > 0 }})
  verified   bool
  @@assert(line_order, {{ this.line_end >= this.line_start }})
}

class MemoryItem {
  category   MemoryCategory
  statement  string
  evidence   EvidenceRef[]
  confidence Confidence
}

class SessionSummary {
  chat_session_id        string
  claude_session_id      string?
  transcript_path        string
  transcript_line_count  int
  turn_count             int
  chat_model             string
  gist                   string
  items                  MemoryItem[]
  writer                 string
  summary_model          string
  schema_version         string
  generated_at           string
}

class SummarizeInput {
  chat_session_id  string
  chat_model       string
  actions_view     string
}

function Summarize(input: SummarizeInput) -> SessionSummary {
  client GCPFlash
  prompt #"
    You distill ONE Claude Code session transcript into a durable, grounded
    memory record for a research-assistant user. The transcript is given as a
    numbered "actions view": each line is prefixed with L<n> (its 1-based line
    index in the source .jsonl). Bash commands, file reads/writes/edits, skill
    invocations, user/assistant text, and truncated tool outputs appear inline.

    Produce a SessionSummary:
    - gist: ONE sentence describing what this whole session was about.
    - items: AT MOST 8 durable takeaways. Each item is ONE concise sentence
      (the statement), a category, a confidence, and >= 1 EvidenceRef. Each
      EvidenceRef MUST cite the L<n> line range it came from (line_start,
      line_end) and quote a SHORT verbatim excerpt from those lines. Do not
      invent quotes; copy exact substrings. Prefer decisions, preferences,
      artifacts created, and open todos over routine chatter.
    - Set writer="baml_gemini", summary_model="{{ input.chat_model }}" is the
      CHAT model, not yours — leave summary_model empty; the host fills it.
    - Set verified=false on every EvidenceRef; the host verifies quotes.
    - schema_version="1c/v1". chat_session_id="{{ input.chat_session_id }}".
      chat_model="{{ input.chat_model }}". transcript_path / line counts /
      turn_count / generated_at / claude_session_id are filled by the host;
      emit empty/zero placeholders.

    Transcript actions view:
    {{ input.actions_view }}

    {{ ctx.output_format }}
  "#
}
