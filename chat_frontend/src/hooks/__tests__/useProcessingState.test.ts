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
});
