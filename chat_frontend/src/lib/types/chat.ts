export interface Message {
  id: string;
  content: string;
  isUser: boolean;
  timestamp: Date;
  status?: "sending" | "sent" | "error";
  messageType?: "text" | "system" | "debug";
  debugEntries?: DebugEntry[];
  bundleId?: number | null;
}

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
