import { useState, useCallback } from "react";
import type { Step, ProcessingState } from "@/lib/types/chat";
import type {
  SearchStartedData,
  SearchCompleteData,
  RouteDecidedData,
  PreludeStepData,
  SelectionStartedData,
  SelectionEvidenceData,
  SelectionDoneData,
} from "@/lib/types/api";

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

// The routing steps a turn reports before its engine runs (the backend's prelude_step labels). They sit above the
// NS steps and above the Container-CC trace.
const PRELUDE_AGENT = "prelude";
const PRELUDE_READING = "Reading your question";
const PRELUDE_CHOOSING = "Choosing an engine";
const PRELUDE_READY = "Vocabulary ready";

function reindex(steps: Step[]): Step[] {
  return steps.map((s, i) => (s.index === i ? s : { ...s, index: i }));
}

function preludeSteps(steps: Step[]): Step[] {
  return steps.filter((s) => s.agentName === PRELUDE_AGENT);
}

/** Complete the routing steps still spinning: those named in `labels`, or all of them. */
function completePrelude(steps: Step[], labels?: string[]): Step[] {
  return steps.map((s) =>
    s.agentName === PRELUDE_AGENT && s.status === "active" && (!labels || labels.includes(s.label))
      ? { ...s, status: "complete" as const }
      : s,
  );
}

interface UseProcessingStateReturn {
  handlePreludeStep: (data: PreludeStepData) => void;
  processingState: ProcessingState;
  handleRouteDecided: (data: RouteDecidedData) => void;
  handleAgentStarted: (agent: string, mode: string) => void;
  handleAgentComplete: (agent: string) => void;
  handleSearchStarted: (data: SearchStartedData) => void;
  handleSearchComplete: (data: SearchCompleteData) => void;
  handleSelectionEvent: (event: string, data: unknown) => void;
  resetProcessing: () => void;
}

/** The agentName owning the pipeline-selection step, so it updates in place. */
const SELECTION_AGENT = "select_pipeline";

function formatTokens(n: number): string {
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

/**
 * Selection's four verdicts, as a line the person waiting can act on.
 *
 * `out_of_scope` deliberately does not read as a failure. It means the selector
 * stepped aside and the agent chooses from the catalog itself, which is what it
 * did before this step existed — most often because the question is not about
 * an RNA pipeline, the only family the atlas covers.
 */
function formatSelectionDoneDetail(d: SelectionDoneData): string {
  const picks = Array.isArray(d.pipelines) ? d.pipelines.filter(Boolean) : [];
  switch (d.verdict) {
    case "chosen":
      return picks.length ? `Using nf-core/${picks[0]}` : "Pipeline chosen";
    case "fork":
      return picks.length
        ? `${picks.map((p) => `nf-core/${p}`).join(" or ")} — asking which`
        : "More than one pipeline fits — asking which";
    case "refused":
      return "These samples cannot answer that question";
    case "out_of_scope":
      return "Choosing from the catalog";
    default:
      return String(d.verdict);
  }
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

  // A routing step: inserted after the routing steps already shown, so one that arrives once the engine's own steps
  // exist still sits above them. "Vocabulary ready" also completes "Reading your question".
  const handlePreludeStep = useCallback((data: PreludeStepData) => {
    const label = typeof data.label === "string" ? data.label.trim() : "";
    if (!label) return;
    setState((prev) => {
      if (prev.steps.some((s) => s.agentName === PRELUDE_AGENT && s.label === label)) return prev;
      const steps = label === PRELUDE_READY ? completePrelude(prev.steps, [PRELUDE_READING]) : prev.steps;
      let at = 0;
      steps.forEach((s, i) => {
        if (s.agentName === PRELUDE_AGENT) at = i + 1;
      });
      const step: Step = {
        index: at,
        label,
        agentName: PRELUDE_AGENT,
        status: label === PRELUDE_READY ? "complete" : "active",
      };
      const onlyPrelude = steps.every((s) => s.agentName === PRELUDE_AGENT);
      return {
        ...prev,
        isProcessing: true,
        steps: reindex([...steps.slice(0, at), step, ...steps.slice(at)]),
        currentStepIndex: onlyPrelude ? at : prev.currentStepIndex >= at ? prev.currentStepIndex + 1 : prev.currentStepIndex,
      };
    });
  }, []);

  // Router decision. Either way the engine is chosen, so "Choosing an engine" completes. A Container-CC route starts
  // the trace below the routing steps with a "Router → container_cc" step carrying the reasoning; for NS/unrelated
  // the NS steps follow on their own (the reasoning is in the Debug panel).
  const handleRouteDecided = useCallback((data: RouteDecidedData) => {
    if (String(data.route) !== CC_MODE) {
      setState((prev) => ({ ...prev, steps: completePrelude(prev.steps, [PRELUDE_CHOOSING]) }));
      return;
    }
    const reasoning = typeof data.reasoning === "string" ? data.reasoning.trim() : "";
    setState((prev) => {
      const steps = reindex([
        ...completePrelude(preludeSteps(prev.steps), [PRELUDE_CHOOSING]),
        {
          index: 0,
          label: "Router → container_cc",
          agentName: "router",
          status: "complete" as const,
          detail: reasoning || undefined,
        },
      ]);
      return { isProcessing: true, steps, currentStepIndex: steps.length - 1, mode: CC_MODE };
    });
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

      // First agent_started: the default steps, below any routing steps.
      if (steps.every((s) => s.agentName === PRELUDE_AGENT)) {
        steps = reindex([...steps, ...buildSteps(DEFAULT_STEPS)]);
      }

      // When we receive a non-empty mode and haven't expanded yet, expand to full config
      if (mode && !expandedMode && STEP_CONFIGS[mode]) {
        const fullConfig = STEP_CONFIGS[mode];
        steps = reindex([...preludeSteps(steps), ...buildSteps(fullConfig)]);
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
      // Mark the agent's step complete AND clear its `detail`. The entity step's end is also the vocabulary's: a
      // routing step still spinning (the pre-run failed and the turn resolved it itself) is done too.
      const steps = prev.steps.map((s) => {
        if (s.agentName === agent) return { ...s, status: "complete" as const, detail: undefined };
        if (agent === "entity" && s.agentName === PRELUDE_AGENT && s.status === "active") {
          return { ...s, status: "complete" as const };
        }
        return s;
      });
      return { ...prev, steps };
    });
  }, []);

  /**
   * The three `selection_*` events, folded into one step that appears when
   * selection starts and closes with its verdict.
   *
   * One handler rather than three because they are one step's lifecycle, and
   * because every consumer has to wire each case into its own event switch —
   * three handlers would triple that wiring for no gain.
   *
   * Unlike search, this never special-cases CC mode: selection only runs on the
   * NS pipeline route, so a CC turn cannot emit these at all. The step is
   * appended rather than matched against STEP_CONFIGS because "pipeline" has no
   * step config — a pipeline turn's stepper is built from what actually happens.
   */
  const handleSelectionEvent = useCallback((event: string, data: unknown) => {
    setState((prev) => {
      const idx = prev.steps.findIndex((s) => s.agentName === SELECTION_AGENT);

      if (event === "selection_started") {
        const d = (data ?? {}) as SelectionStartedData;
        const n = typeof d.n_uids === "number" ? d.n_uids : 0;
        // n_uids is 0 on the accession path, where there is nothing to profile.
        const detail = n > 0 ? `Reading ${n} sample${n === 1 ? "" : "s"}…` : "Reading the request…";
        if (idx >= 0) {
          const steps = prev.steps.map((s, i) =>
            i === idx ? { ...s, status: "active" as const, detail } : s,
          );
          return { ...prev, isProcessing: true, steps };
        }
        const step: Step = {
          index: prev.steps.length,
          label: "Choosing a pipeline",
          agentName: SELECTION_AGENT,
          status: "active",
          detail,
        };
        return { ...prev, isProcessing: true, steps: [...prev.steps, step] };
      }

      if (idx < 0) return prev;

      if (event === "selection_evidence_ready") {
        const d = (data ?? {}) as SelectionEvidenceData;
        const detail =
          typeof d.est_tokens === "number"
            ? `Weighing the evidence · ~${formatTokens(d.est_tokens)} tokens`
            : "Weighing the evidence…";
        const steps = prev.steps.map((s, i) => (i === idx ? { ...s, detail } : s));
        return { ...prev, steps };
      }

      if (event === "selection_done") {
        const d = (data ?? {}) as SelectionDoneData;
        const steps = prev.steps.map((s, i) =>
          i === idx
            ? { ...s, status: "complete" as const, detail: formatSelectionDoneDetail(d) }
            : s,
        );
        return { ...prev, steps };
      }

      return prev;
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
    handlePreludeStep,
    handleRouteDecided,
    handleAgentStarted,
    handleAgentComplete,
    handleSearchStarted,
    handleSearchComplete,
    handleSelectionEvent,
    resetProcessing,
  };
}
