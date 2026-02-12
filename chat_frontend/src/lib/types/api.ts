// POST /assistant/query/async/ response
export interface AsyncQueryResponse {
  task_id: string;
  session_id: string;
}

// WebSocket progress event envelope
export interface ProgressEvent {
  event: string;
  data: AgentStartedData | AgentCompleteData | QueryCompleteData | QueryErrorData | Record<string, unknown>;
}

export interface AgentStartedData {
  agent: string;
  mode: string;
}

export interface AgentCompleteData {
  agent: string;
  summary: Record<string, unknown> | string | null;
}

export interface QueryCompleteData {
  reply: string;
  debug: Record<string, string>;
  bundle_id: number;
}

export interface QueryErrorData {
  error: string;
  agent?: string;
}

// GET /assistant/me/ response
export interface MeResponse {
  username: string;
  is_admin: boolean;
}

// GET /assistant/test-cases/ response
export interface TestCasesResponse {
  total: number;
  test_cases: TestCase[];
}

// GET /assistant/sessions/{id}/ response
export interface SessionResponse {
  session_id: string;
  created_at: string;
  query_count: number;
  has_results: boolean;
}

export interface TestCase {
  id: string;
  prompt: string;
}
