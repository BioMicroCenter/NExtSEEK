// POST /assistant/query/async/ response
export interface AsyncQueryResponse {
  task_id: string;
  session_id: string;
}

// WebSocket progress event envelope
export interface ProgressEvent {
  event: string;
  data:
    | AgentStartedData
    | AgentCompleteData
    | SearchStartedData
    | SearchCompleteData
    | QueryCompleteData
    | QueryErrorData
    | RouteDecidedData
    | CcTurnMetaData
    | Record<string, unknown>;
}

/** Top-level router decision (emitted before NS/CC executes). */
export interface RouteDecidedData {
  route: string;
  model_class?: string;
  source?: string;
  reasoning?: string;
}

/** Container-CC turn parameters, emitted just before the CC turn starts. */
export interface CcTurnMetaData {
  model_id?: string;
  cc_session_id?: string | null;
  budget_usd?: number;
  turn_timeout_s?: number;
}

export interface AgentStartedData {
  agent: string;
  mode: string;
}

export interface AgentCompleteData {
  agent: string;
  summary: Record<string, unknown> | string | null;
}

/**
 * Emitted by chat_nextseek's orchestrator (and planner-loop execution)
 * when an agent kicks off a side-effectful sub-step. Shape varies by
 * `source`:
 *   - "neo4j"    → carries `cypher`
 *   - "api"      → carries `endpoint`, `method`
 *   - "reporter" → carries `project`, `summary_mode`
 */
export interface SearchStartedData {
  source: "neo4j" | "api" | "reporter" | string;
  cypher?: string;
  endpoint?: string;
  method?: string;
  project?: string | number;
  summary_mode?: string;
  /** Container-CC: a one-line summary of the tool input (command / file / thought). */
  detail?: string;
  [extra: string]: unknown;
}

/**
 * Emitted on completion of the same sub-step. Neo4j carries `count` + `error`;
 * API carries `endpoint` + HTTP `status`; reporter may carry row counts.
 */
export interface SearchCompleteData {
  source: "neo4j" | "api" | "reporter" | string;
  count?: number;
  ok?: boolean;
  error?: string | null;
  endpoint?: string;
  status?: number;
  detail?: string;
  [extra: string]: unknown;
}

import type { Artifact, CCTrace } from "./chat";

export interface QueryCompleteData {
  reply: string;
  debug: Record<string, unknown>;
  bundle_id: number;
  artifacts?: Artifact[] | null;
  cc_traces?: CCTrace[];
  mode?: "cc" | "ns";
  session_id?: string;
}

export interface QueryErrorData {
  error: string;
  agent?: string;
  session_id?: string;
  reason?: string;
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
  artifacts?: Artifact[] | null;
  cc_traces?: CCTrace[];
}

// GET /assistant/sessions/{id}/?include=turns response
export interface SessionDetailWithTurns extends SessionResponse {
  title?: string | null;
  turns?: Turn[] | null;
}
