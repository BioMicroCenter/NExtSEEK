export interface CCTraceStep {
  line: number;
  kind: "bash" | "write" | "edit" | "read" | "skill" | "tool" | "text";
  tool?: string;
  detail?: string;
  text?: string;
  action?: string;
  status?: string;
}

export interface CCTrace {
  schema_version: string;
  cc_session_id: string;
  ts: string;
  transcript_line_count: number;
  turn_count: number;
  num_turns?: number;
  duration_ms?: number;
  cost_usd?: number;
  steps: CCTraceStep[];
  tools_used: Record<string, number>;
  files_created: string[];
  files_modified: string[];
}

export interface Message {
  id: string;
  content: string;
  isUser: boolean;
  timestamp: Date;
  status?: "sending" | "sent" | "error";
  messageType?: "text" | "system" | "debug";
  debugEntries?: DebugEntry[];
  bundleId?: number | null;
  artifacts?: Artifact[] | null;
  ccTraces?: CCTrace[];
  mode?: string;
}

export interface ArtifactTable {
  artifact_type: "table";
  key: string;
  label: string;
  columns: string[];
  data: Record<string, unknown>[];
  truncated?: boolean;
  total_rows?: number;
}

export interface ArtifactFile {
  artifact_type: "file";
  key: string;
  label: string;
  file_format: string;
}

export interface ArtifactPreviewSheet {
  name: string;
  columns: string[];
  data: Record<string, unknown>[];
  total_rows: number;
}

export interface ArtifactPreview {
  artifact_type: "preview";
  key: string;
  label: string;
  sheets: ArtifactPreviewSheet[];
}

export type Artifact = ArtifactTable | ArtifactFile | ArtifactPreview;

export interface Step {
  index: number;
  label: string;
  agentName: string;
  status: "pending" | "active" | "complete" | "error";
  /**
   * Optional sub-status describing the most recent side-effect the agent
   * initiated. Set by `search_started` / `search_complete` events emitted
   * by chat_nextseek when an agent dispatches a Neo4j query, REST call,
   * or project report. Cleared on the next agent transition.
   *
   * Examples: "Querying Neo4j: MATCH (s:Study)…", "Neo4j: 12 rows",
   *           "Calling /api/sample/search (POST)", "API: 200 OK · 47 rows".
   */
  detail?: string;
}

export interface ProcessingState {
  isProcessing: boolean;
  steps: Step[];
  currentStepIndex: number;
  mode: string | null;
}

export interface DebugEntry {
  agent: string;
  summary: string;
  timestamp: Date;
}

export interface DebugData {
  entries: DebugEntry[];
  bundleId: number | null;
  query: string;
}
