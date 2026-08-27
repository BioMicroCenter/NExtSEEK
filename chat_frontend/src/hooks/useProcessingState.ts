import { useState, useCallback } from "react";
import type { Step, ProcessingState } from "@/lib/types/chat";
import type { SearchStartedData, SearchCompleteData, RouteDecidedData } from "@/lib/types/api";

// Container-CC turns don't map onto a fixed pipeline — they run an open-ended
// sequence of tool calls. So CC uses a dynamic "trace" mode (one step appended
// per tool call / thinking block, led by the router decision) instead of the
// NS STEP_CONFIGS below.
const CC_MODE = "container_cc";

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
  graph_query: [
    { label: "Extracting entities", agentName: "entity" },
    { label: "Planning query", agentName: "parser" },
    { label: "Running graph query", agentName: "graph" },
    { label: "Summarizing results", agentName: "chatter" },
  ],
  report_generation: [
    { label: "Extracting entities", agentName: "entity" },
    { label: "Planning query", agentName: "parser" },
    { label: "Building report", agentName: "reporter" },
    { label: "Writing export", agentName: "report_writer" },
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

/** CC step label: the tool name as-is, except the synthetic "thinking" source. */
function ccStepLabel(source: string): string {
  return source === "thinking" ? "Thinking" : source;
}

interface UseProcessingStateReturn {
  processingState: ProcessingState;
  handleRouteDecided: (data: RouteDecidedData) => void;
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

/**
 * Map a `search_started`/`search_complete` event's `source` to the agentName
 * of the step that semantically owns the side-effect. Used to attach details
 * to the correct row regardless of which step is currently "active" — the
 * orchestrator can emit agent_complete BEFORE the matching search_started
 * (graph plans the cypher first, then the neo4j call runs).
 *
 * Add a row here when STEP_CONFIGS gains a new mode whose search-emitting
 * agent has a different name (e.g. a new "pipeline" mode with its own step).
 */
const SEARCH_SOURCE_TO_AGENT: Record<string, string> = {
  neo4j: "graph",
  api: "http",
  reporter: "reporter",
};

function findSearchTargetIndex(steps: Step[], source: string): number {
  const preferred = SEARCH_SOURCE_TO_AGENT[source];
  if (preferred) {
    const i = steps.findIndex((s) => s.agentName === preferred);
    if (i >= 0) return i;
  }
  const active = steps.findIndex((s) => s.status === "active");
  if (active >= 0) return active;
  const pending = steps.findIndex((s) => s.status === "pending");
  if (pending >= 0) return pending;
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === "complete") return i;
  }
  return -1;
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

  // Router decision — starts the CC trace with a "Router → container_cc" step
  // carrying the reasoning. For NS/unrelated the stepper is unchanged (the NS
  // pipeline speaks for itself; the reasoning is in the Debug panel).
  const handleRouteDecided = useCallback((data: RouteDecidedData) => {
    if (String(data.route) !== CC_MODE) return;
    const reasoning = typeof data.reasoning === "string" ? data.reasoning.trim() : "";
    setState(() => ({
      isProcessing: true,
      steps: [{
        index: 0,
        label: "Router → container_cc",
        agentName: "router",
        status: "complete" as const,
        detail: reasoning || undefined,
      }],
      currentStepIndex: 0,
      mode: CC_MODE,
    }));
  }, []);

  const handleAgentStarted = useCallback((agent: string, mode: string) => {
    setState((prev) => {
      // CC trace mode: the trace is built from search_started tool steps, not
      // the NS STEP_CONFIGS. Just keep processing and stay in CC mode.
      if (agent === CC_MODE || prev.mode === CC_MODE) {
        return { ...prev, isProcessing: true, mode: CC_MODE };
      }

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
      if (prev.mode === CC_MODE) {
        // CC turn wound down: close any step still spinning.
        const steps = prev.steps.map((s) =>
          s.status === "active" ? { ...s, status: "complete" as const } : s,
        );
        return { ...prev, steps };
      }
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

  const handleSearchStarted = useCallback((data: SearchStartedData) => {
    setState((prev) => {
      if (prev.mode === CC_MODE) {
        // Append one step per tool call / thinking block; detail is the command
        // / file / thought text the backend already formatted.
        const source = String(data.source);
        const detail = typeof data.detail === "string" ? data.detail : undefined;
        const step: Step = {
          index: prev.steps.length,
          label: ccStepLabel(source),
          agentName: source,
          status: "active",
          detail,
        };
        return { ...prev, steps: [...prev.steps, step] };
      }
      if (prev.steps.length === 0) return prev;
      const detail = formatSearchStartedDetail(data);
      const targetIdx = findSearchTargetIndex(prev.steps, String(data.source));
      if (targetIdx < 0) return prev;
      const steps = prev.steps.map((s, i) => (i === targetIdx ? { ...s, detail } : s));
      return { ...prev, steps };
    });
  }, []);

  const handleSearchComplete = useCallback((data: SearchCompleteData) => {
    setState((prev) => {
      if (prev.mode === CC_MODE) {
        const source = String(data.source);
        const ok = data.ok !== false;
        // Close the most recent still-active step for this source (its command
        // detail stays visible; only the status flips).
        let targetIdx = -1;
        for (let i = prev.steps.length - 1; i >= 0; i--) {
          if (prev.steps[i].agentName === source && prev.steps[i].status === "active") {
            targetIdx = i;
            break;
          }
        }
        if (targetIdx < 0) return prev;
        const steps = prev.steps.map((s, i) =>
          i === targetIdx ? { ...s, status: ok ? ("complete" as const) : ("error" as const) } : s,
        );
        return { ...prev, steps };
      }
      if (prev.steps.length === 0) return prev;
      const detail = formatSearchCompleteDetail(data);
      const targetIdx = findSearchTargetIndex(prev.steps, String(data.source));
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
    handleRouteDecided,
    handleAgentStarted,
    handleAgentComplete,
    handleSearchStarted,
    handleSearchComplete,
    resetProcessing,
  };
}
