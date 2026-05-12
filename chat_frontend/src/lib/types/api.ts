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

import type { Artifact } from "./chat";

export interface QueryCompleteData {
  reply: string;
  debug: Record<string, unknown>;
  bundle_id: number;
  artifacts?: Artifact[] | null;
  session_id?: string;
}

export interface QueryErrorData {
  error: string;
  agent?: string;
  session_id?: string;
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

// GET /assistant/sessions/ row
export interface SessionListItem {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  query_count: number;
  preview: string;
}

// GET /assistant/sessions/ response
export interface SessionListResponse {
  total: number;
  sessions: SessionListItem[];
}

// One turn from a session's results_history projection
export interface Turn {
  bundle_id: number;
  user_query: string;
  reply: string;
  mode: string;
  ts?: string | null;
}

// GET /assistant/sessions/{id}/?include=turns response
export interface SessionDetailWithTurns extends SessionResponse {
  title?: string | null;
  turns?: Turn[] | null;
}
