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
}

export interface ArtifactTable {
  artifact_type: "table";
  key: string;
  label: string;
  columns: string[];
  data: Record<string, unknown>[];
}

export interface ArtifactFile {
  artifact_type: "file";
  key: string;
  label: string;
  file_format: string;
}

export type Artifact = ArtifactTable | ArtifactFile;

export interface Step {
  index: number;
  label: string;
  agentName: string;
  status: "pending" | "active" | "complete" | "error";
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
