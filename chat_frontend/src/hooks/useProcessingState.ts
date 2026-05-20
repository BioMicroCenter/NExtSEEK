import { useState, useCallback } from "react";
import type { Step, ProcessingState } from "@/lib/types/chat";
import type { SearchStartedData, SearchCompleteData } from "@/lib/types/api";

const STEP_CONFIGS: Record<string, { label: string; agentName: string }[]> = {
  new_search: [
    { label: "Extracting entities", agentName: "entity" },
    { label: "Planning query", agentName: "parser" },
    { label: "Building request", agentName: "api" },
    { label: "Executing search", agentName: "http" },
    { label: "Summarizing results", agentName: "chatter" },
  ],
  refine_last_search: [
    { label: "Extracting entities", agentName: "entity" },
    { label: "Planning query", agentName: "parser" },
    { label: "Building request", agentName: "api" },
    { label: "Executing search", agentName: "http" },
    { label: "Summarizing results", agentName: "chatter" },
  ],
  reporter: [
    { label: "Extracting entities", agentName: "entity" },
    { label: "Planning query", agentName: "parser" },
    { label: "Running report", agentName: "reporter" },
    { label: "Summarizing results", agentName: "chatter" },
  ],
  ask_about_last_results: [
    { label: "Planning query", agentName: "parser" },
    { label: "Searching memory", agentName: "memory" },
  ],
  system_question: [{ label: "Processing", agentName: "parser" }],
  unsupported: [{ label: "Processing", agentName: "parser" }],
};

const DEFAULT_STEPS: { label: string; agentName: string }[] = [
  { label: "Extracting entities", agentName: "entity" },
  { label: "Planning query", agentName: "parser" },
];

function buildSteps(
  configs: { label: string; agentName: string }[],
): Step[] {
  return configs.map((c, i) => ({
    index: i,
    label: c.label,
    agentName: c.agentName,
    status: "pending" as const,
  }));
}

interface UseProcessingStateReturn {
  processingState: ProcessingState;
  handleAgentStarted: (agent: string, mode: string) => void;
  handleAgentComplete: (agent: string) => void;
  handleSearchStarted: (data: SearchStartedData) => void;
  handleSearchComplete: (data: SearchCompleteData) => void;
  resetProcessing: () => void;
}

/** One-line summary of an in-flight side-effect, surfaced as Step.detail. */
function formatSearchStartedDetail(d: SearchStartedData): string {
  switch (d.source) {
    case "neo4j": {
      const cy = typeof d.cypher === "string" ? d.cypher.trim().replace(/\s+/g, " ") : "";
      const head = cy.length > 80 ? cy.slice(0, 77) + "…" : cy;
      return head ? `Querying Neo4j: ${head}` : "Querying Neo4j…";
    }
    case "api": {
      const method = d.method ? String(d.method).toUpperCase() : "GET";
      const endpoint = d.endpoint ? String(d.endpoint) : "(unknown endpoint)";
      return `Calling ${endpoint} (${method})`;
    }
    case "reporter": {
      const project = d.project != null ? String(d.project) : "?";
      const mode = d.summary_mode ? String(d.summary_mode) : "summary";
      return `Running ${mode} report on project ${project}`;
    }
    default:
      return `Running ${d.source}…`;
  }
}

function formatSearchCompleteDetail(d: SearchCompleteData): string {
  const isErr = d.error != null || d.ok === false;
  switch (d.source) {
    case "neo4j": {
      if (isErr) return `Neo4j error: ${d.error ?? "unknown"}`;
      const n = typeof d.count === "number" ? d.count : "?";
      return `Neo4j: ${n} row${n === 1 ? "" : "s"}`;
    }
    case "api": {
      if (isErr) return `API error: ${d.error ?? "unknown"}`;
      const parts: string[] = ["API"];
      if (typeof d.status === "number") parts.push(String(d.status));
      if (typeof d.count === "number") parts.push(`${d.count} row${d.count === 1 ? "" : "s"}`);
      else if (d.endpoint) parts.push(String(d.endpoint));
      return parts.join(" · ");
    }
    case "reporter": {
      if (isErr) return `Reporter error: ${d.error ?? "unknown"}`;
      return typeof d.count === "number" ? `Report ready · ${d.count} row${d.count === 1 ? "" : "s"}` : "Report ready";
    }
    default:
      return isErr ? `${d.source} error: ${d.error ?? "unknown"}` : `${d.source} done`;
  }
}

export function useProcessingState(): UseProcessingStateReturn {
  const [state, setState] = useState<ProcessingState>({
    isProcessing: false,
    steps: [],
    currentStepIndex: -1,
    mode: null,
  });

  const handleAgentStarted = useCallback((agent: string, mode: string) => {
    setState((prev) => {
      let steps = prev.steps;
      let expandedMode = prev.mode;

      // First agent_started: initialize with default steps
      if (steps.length === 0) {
        steps = buildSteps(DEFAULT_STEPS);
      }

      // When we receive a non-empty mode and haven't expanded yet, expand to full config
      if (mode && !expandedMode && STEP_CONFIGS[mode]) {
        const fullConfig = STEP_CONFIGS[mode];
        steps = buildSteps(fullConfig);
        expandedMode = mode;
        // Mark entity and parser as complete (they've already run)
        steps = steps.map((s) =>
          s.agentName === "entity" || s.agentName === "parser"
            ? { ...s, status: "complete" as const }
            : s,
        );
      }

      // Find the step for this agent and mark it active
      const stepIndex = steps.findIndex((s) => s.agentName === agent);
      if (stepIndex >= 0) {
        steps = steps.map((s, i) =>
          i === stepIndex ? { ...s, status: "active" as const } : s,
        );
      }

      return {
        isProcessing: true,
        steps,
        currentStepIndex: stepIndex >= 0 ? stepIndex : prev.currentStepIndex,
        mode: expandedMode,
      };
    });
  }, []);

  const handleAgentComplete = useCallback((agent: string) => {
    setState((prev) => {
      // Mark the agent's step complete AND clear its `detail` — the side-effect
      // it was reporting has finished alongside the agent itself.
      const steps = prev.steps.map((s) =>
        s.agentName === agent
          ? { ...s, status: "complete" as const, detail: undefined }
          : s,
      );
      return { ...prev, steps };
    });
  }, []);

  /**
   * Attach a sub-status to whichever step is currently active. If no step is
   * active (e.g. an out-of-order event), store the detail on the first pending
   * step so it still shows up rather than disappearing silently.
   */
  const handleSearchStarted = useCallback((data: SearchStartedData) => {
    const detail = formatSearchStartedDetail(data);
    setState((prev) => {
      if (prev.steps.length === 0) return prev;
      const activeIdx = prev.steps.findIndex((s) => s.status === "active");
      const targetIdx =
        activeIdx >= 0
          ? activeIdx
          : prev.steps.findIndex((s) => s.status === "pending");
      if (targetIdx < 0) return prev;
      const steps = prev.steps.map((s, i) => (i === targetIdx ? { ...s, detail } : s));
      return { ...prev, steps };
    });
  }, []);

  const handleSearchComplete = useCallback((data: SearchCompleteData) => {
    const detail = formatSearchCompleteDetail(data);
    setState((prev) => {
      if (prev.steps.length === 0) return prev;
      // Prefer the active step; fall back to the most recent step that already
      // has a detail set by `search_started` (handles a late-firing complete).
      let targetIdx = prev.steps.findIndex((s) => s.status === "active");
      if (targetIdx < 0) {
        for (let i = prev.steps.length - 1; i >= 0; i--) {
          if (prev.steps[i].detail) { targetIdx = i; break; }
        }
      }
      if (targetIdx < 0) return prev;
      const steps = prev.steps.map((s, i) => (i === targetIdx ? { ...s, detail } : s));
      return { ...prev, steps };
    });
  }, []);

  const resetProcessing = useCallback(() => {
    setState({
      isProcessing: false,
      steps: [],
      currentStepIndex: -1,
      mode: null,
    });
  }, []);

  return {
    processingState: state,
    handleAgentStarted,
    handleAgentComplete,
    handleSearchStarted,
    handleSearchComplete,
    resetProcessing,
  };
}
