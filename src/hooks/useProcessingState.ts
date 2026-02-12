import { useState, useCallback } from "react";
import type { Step, ProcessingState } from "@/lib/types/chat";

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
  resetProcessing: () => void;
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
      const steps = prev.steps.map((s) =>
        s.agentName === agent ? { ...s, status: "complete" as const } : s,
      );
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

  return { processingState: state, handleAgentStarted, handleAgentComplete, resetProcessing };
}
