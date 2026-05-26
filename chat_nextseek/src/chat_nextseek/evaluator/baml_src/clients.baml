// Learn more about clients at https://docs.boundaryml.com/docs/snippets/clients/overview

// ─── Fast / Cheap Clients ────────────────────────────
client<llm> FastCheap {
  provider openai
  retry_policy Exponential
  options {
    model "gpt-5-mini-2025-08-07"
    api_key env.OPENAI_API_KEY
  }
}

client<llm> FastestCheap {
  provider openai
  retry_policy Exponential
  options {
    model "gpt-5-nano-2025-08-07"
    api_key env.OPENAI_API_KEY
  }
}

client<llm> GeminiFast {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-3-flash-preview"
    api_key env.GCP_API_KEY
  }
}

// ─── Reasoning Clients ───────────────────────────────
client<llm> Reasoner {
  provider openai
  retry_policy Exponential
  options {
    model "gpt-5.1-2025-11-13"
    api_key env.OPENAI_API_KEY
  }
}

client<llm> GeminiPro {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-3-pro-preview"
    api_key env.GCP_API_KEY
  }
}

// ─── Fallback Clients ────────────────────────────────
client<llm> FastFallback {
  provider fallback
  options {
    strategy [FastCheap, GeminiFast, FastestCheap]
  }
}

client<llm> ReasonerFallback {
  provider fallback
  options {
    strategy [Reasoner, GeminiPro]
  }
}

// ─── Retry Policies ──────────────────────────────────
// https://docs.boundaryml.com/docs/snippets/clients/retry
retry_policy Constant {
  max_retries 3
  strategy {
    type constant_delay
    delay_ms 200
  }
}

retry_policy Exponential {
  max_retries 2
  strategy {
    type exponential_backoff
    delay_ms 300
    multiplier 1.5
    max_delay_ms 10000
  }
}
