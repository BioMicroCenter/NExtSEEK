import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatRoute } from "../useChatRoute";

function setMetaBasename(value: string | null) {
  document.querySelectorAll('meta[name="chat-basename"]').forEach((n) => n.remove());
  if (value !== null) {
    const m = document.createElement("meta");
    m.setAttribute("name", "chat-basename");
    m.setAttribute("content", value);
    document.head.appendChild(m);
  }
}

describe("useChatRoute", () => {
  beforeEach(() => {
    setMetaBasename(null);
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    setMetaBasename(null);
  });

  it("reads sessionIdFromUrl from the path when basename is /", () => {
    window.history.pushState({}, "", "/chat/abc-123");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBe("abc-123");
  });

  it("reads sessionIdFromUrl under a custom basename", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/chat/def-456");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBe("def-456");
  });

  it("returns null when path has no /chat/ segment", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBeNull();
  });

  it("push(uuid) updates the URL under basename", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/");
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      result.current.push("xyz-789");
    });
    expect(window.location.pathname).toBe("/assistant/chat/xyz-789");
  });

  it("push(null) returns to the basename root", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/chat/abc");
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      result.current.push(null);
    });
    expect(window.location.pathname).toBe("/assistant/");
  });

  it("popstate updates sessionIdFromUrl", () => {
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      window.history.pushState({}, "", "/chat/aaa");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current.sessionIdFromUrl).toBe("aaa");
  });
});
