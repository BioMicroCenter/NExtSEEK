import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useProcessingState } from "../useProcessingState";

describe("useProcessingState", () => {
  it("has initial state with isProcessing=false and empty steps", () => {
    const { result } = renderHook(() => useProcessingState());

    expect(result.current.processingState.isProcessing).toBe(false);
    expect(result.current.processingState.steps).toHaveLength(0);
  });

  it("initializes 2 default steps on first agent_started (entity)", () => {
    const { result } = renderHook(() => useProcessingState());

    act(() => {
      result.current.handleAgentStarted("entity", "");
    });

    expect(result.current.processingState.isProcessing).toBe(true);
    expect(result.current.processingState.steps).toHaveLength(2);
    expect(result.current.processingState.steps[0].agentName).toBe("entity");
    expect(result.current.processingState.steps[0].status).toBe("active");
    expect(result.current.processingState.steps[1].agentName).toBe("parser");
  });

  it("expands to 5 steps on agent_started with mode=new_search", () => {
    const { result } = renderHook(() => useProcessingState());

    act(() => {
      result.current.handleAgentStarted("entity", "");
    });

    act(() => {
      result.current.handleAgentStarted("api", "new_search");
    });

    expect(result.current.processingState.steps).toHaveLength(5);
    // entity and parser should be marked complete
    expect(result.current.processingState.steps[0].status).toBe("complete");
    expect(result.current.processingState.steps[1].status).toBe("complete");
    // api should be active
    expect(result.current.processingState.steps[2].status).toBe("active");
    expect(result.current.processingState.mode).toBe("new_search");
  });

  it("expands to 4 steps on agent_started with mode=reporter", () => {
    const { result } = renderHook(() => useProcessingState());

    act(() => {
      result.current.handleAgentStarted("entity", "");
    });

    act(() => {
      result.current.handleAgentStarted("reporter", "reporter");
    });

    expect(result.current.processingState.steps).toHaveLength(4);
    expect(result.current.processingState.mode).toBe("reporter");
  });

  it("handleAgentComplete advances step status to complete", () => {
    const { result } = renderHook(() => useProcessingState());

    act(() => {
      result.current.handleAgentStarted("entity", "");
    });

    act(() => {
      result.current.handleAgentComplete("entity");
    });

    expect(result.current.processingState.steps[0].status).toBe("complete");
  });

  it("resetProcessing clears all state", () => {
    const { result } = renderHook(() => useProcessingState());

    act(() => {
      result.current.handleAgentStarted("entity", "");
    });
    expect(result.current.processingState.isProcessing).toBe(true);

    act(() => {
      result.current.resetProcessing();
    });

    expect(result.current.processingState.isProcessing).toBe(false);
    expect(result.current.processingState.steps).toHaveLength(0);
    expect(result.current.processingState.mode).toBeNull();
  });

  // --- Container-CC trace mode (#4) ---

  it("CC route_decided starts the trace with a router step", () => {
    const { result } = renderHook(() => useProcessingState());
    act(() => result.current.handleRouteDecided({ route: "container_cc", reasoning: "needs code" }));
    const st = result.current.processingState;
    expect(st.mode).toBe("container_cc");
    expect(st.steps).toHaveLength(1);
    expect(st.steps[0].agentName).toBe("router");
    expect(st.steps[0].label).toBe("Router → container_cc");
    expect(st.steps[0].detail).toBe("needs code");
    expect(st.steps[0].status).toBe("complete");
  });

  it("CC search_started appends a tool step with detail; search_complete closes it and keeps the detail", () => {
    const { result } = renderHook(() => useProcessingState());
    act(() => result.current.handleRouteDecided({ route: "container_cc", reasoning: "" }));
    act(() => result.current.handleSearchStarted({ source: "Bash", detail: "ls -la" }));
    let st = result.current.processingState;
    expect(st.steps).toHaveLength(2);
    expect(st.steps[1].agentName).toBe("Bash");
    expect(st.steps[1].detail).toBe("ls -la");
    expect(st.steps[1].status).toBe("active");
    act(() => result.current.handleSearchComplete({ source: "Bash" }));
    st = result.current.processingState;
    expect(st.steps[1].status).toBe("complete");
    expect(st.steps[1].detail).toBe("ls -la");
  });

  it("CC search_complete with ok=false marks the step as error", () => {
    const { result } = renderHook(() => useProcessingState());
    act(() => result.current.handleRouteDecided({ route: "container_cc", reasoning: "" }));
    act(() => result.current.handleSearchStarted({ source: "Bash", detail: "boom" }));
    act(() => result.current.handleSearchComplete({ source: "Bash", ok: false }));
    expect(result.current.processingState.steps[1].status).toBe("error");
  });

  it("CC repeated tools each get their own step with unique indices", () => {
    const { result } = renderHook(() => useProcessingState());
    act(() => result.current.handleRouteDecided({ route: "container_cc", reasoning: "" }));
    act(() => result.current.handleSearchStarted({ source: "Bash", detail: "a" }));
    act(() => result.current.handleSearchComplete({ source: "Bash" }));
    act(() => result.current.handleSearchStarted({ source: "Bash", detail: "b" }));
    const st = result.current.processingState;
    expect(st.steps).toHaveLength(3);
    expect(new Set(st.steps.map((s) => s.index)).size).toBe(3);
    expect(st.steps[1].detail).toBe("a");
    expect(st.steps[2].detail).toBe("b");
  });

  it("NS route_decided leaves the stepper untouched", () => {
    const { result } = renderHook(() => useProcessingState());
    act(() => result.current.handleRouteDecided({ route: "nextseek_query", reasoning: "lookup" }));
    expect(result.current.processingState.steps).toHaveLength(0);
    expect(result.current.processingState.mode).toBeNull();
  });
});
