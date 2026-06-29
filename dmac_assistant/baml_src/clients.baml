// Shared GCP client for all DMAC BAML functions (router + e2e judges).
// Consolidated from src/dmac_assistant/router/baml_src/clients.baml and
// tools/e2e/baml_src/clients.baml (2026-05-20).

retry_policy Exponential {
  max_retries 2
  strategy {
    type exponential_backoff
    delay_ms 300
    multiplier 1.5
    max_delay_ms 10000
  }
}

client<llm> GCPReasoner {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-3.1-pro-preview"
    api_key env.GCP_API_KEY
  }
}

// Step 1c: cheap flash-tier Gemini for host-side session summarization.
// Off the Opus-only Bedrock proxy; uses the GCP_API_KEY already in the bridge env.
client<llm> GCPFlash {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-3.5-flash"
    api_key env.GCP_API_KEY
  }
}
