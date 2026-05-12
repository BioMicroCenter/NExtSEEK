import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMessages } from "../useMessages";

describe("useMessages", () => {
  it("addUserMessage creates a message with isUser=true", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("hello");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(true);
    expect(result.current.messages[0].content).toBe("hello");
    expect(result.current.messages[0].messageType).toBe("text");
  });

  it("addAssistantMessage creates a message with isUser=false", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addAssistantMessage("response");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(false);
    expect(result.current.messages[0].content).toBe("response");
    expect(result.current.messages[0].messageType).toBe("text");
  });

  it("addSystemMessage creates a message with messageType='system'", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addSystemMessage("system notice");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(false);
    expect(result.current.messages[0].messageType).toBe("system");
  });

  it("clearMessages empties the array", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("one");
      result.current.addAssistantMessage("two");
    });
    expect(result.current.messages).toHaveLength(2);

    act(() => {
      result.current.clearMessages();
    });
    expect(result.current.messages).toHaveLength(0);
  });
});

describe("useMessages — hydrateFromTurns", () => {
  it("replaces messages with paired user+assistant entries per turn", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("stale");
    });

    act(() => {
      result.current.hydrateFromTurns([
        { bundle_id: 1, user_query: "first", reply: "one", mode: "x" },
        { bundle_id: 2, user_query: "second", reply: "two", mode: "x" },
      ]);
    });

    expect(result.current.messages).toHaveLength(4);
    expect(result.current.messages[0].isUser).toBe(true);
    expect(result.current.messages[0].content).toBe("first");
    expect(result.current.messages[1].isUser).toBe(false);
    expect(result.current.messages[1].content).toBe("one");
    expect(result.current.messages[1].bundleId).toBe(1);
    expect(result.current.messages[2].content).toBe("second");
    expect(result.current.messages[3].bundleId).toBe(2);
  });

  it("hydrate of empty turns clears messages", () => {
    const { result } = renderHook(() => useMessages());
    act(() => {
      result.current.addUserMessage("stale");
    });
    act(() => {
      result.current.hydrateFromTurns([]);
    });
    expect(result.current.messages).toHaveLength(0);
  });
});
