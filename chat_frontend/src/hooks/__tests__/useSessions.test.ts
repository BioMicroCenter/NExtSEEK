import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useSessions } from "../useSessions";
import type { SessionListItem, Turn } from "@/lib/types/api";

import type { SessionServiceLike } from "../useSessions";

type ServiceMock = SessionServiceLike & {
  listSessions: ReturnType<typeof vi.fn>;
  renameSession: ReturnType<typeof vi.fn>;
  deleteSession: ReturnType<typeof vi.fn>;
  fetchSessionTurns: ReturnType<typeof vi.fn>;
};

function makeService(initialSessions: SessionListItem[] = []): ServiceMock {
  return {
    listSessions: vi.fn().mockResolvedValue({ total: initialSessions.length, sessions: initialSessions }),
    renameSession: vi.fn(),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    fetchSessionTurns: vi.fn(),
  } as ServiceMock;
}

function makeHydrate() {
  return vi.fn();
}

const s = (id: string, title = id): SessionListItem => ({
  session_id: id,
  title,
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  query_count: 0,
  preview: "",
});

describe("useSessions", () => {
  beforeEach(() => {
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the list on mount", async () => {
    const service = makeService([s("a"), s("b")]);
    const { result } = renderHook(() => useSessions({ service, hydrate: makeHydrate(), onRouteChange: vi.fn() }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(2));
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.pendingNewChat).toBe(true);
  });

  it("newChat() clears active id, sets pendingNewChat, and pushes null route", async () => {
    const service = makeService([s("a")]);
    const onRouteChange = vi.fn();
    const hydrate = makeHydrate();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));

    act(() => { result.current.setActiveImmediately("a"); });
    await waitFor(() => expect(result.current.activeSessionId).toBe("a"));

    act(() => { result.current.newChat(); });
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.pendingNewChat).toBe(true);
    expect(onRouteChange).toHaveBeenCalledWith(null);
  });

  it("setActive fetches turns and hydrates", async () => {
    const turns: Turn[] = [{ bundle_id: 1, user_query: "hi", reply: "hello", mode: "x" }];
    const service = makeService([s("a")]);
    service.fetchSessionTurns.mockResolvedValue({
      session_id: "a", created_at: "x", query_count: 1, has_results: true, title: "a", turns,
    });
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));

    await act(async () => {
      await result.current.setActive("a");
    });
    expect(service.fetchSessionTurns).toHaveBeenCalledWith("a");
    expect(hydrate).toHaveBeenCalledWith(turns);
    expect(result.current.activeSessionId).toBe("a");
    expect(result.current.pendingNewChat).toBe(false);
    expect(onRouteChange).toHaveBeenLastCalledWith("a");
  });

  it("promoteCreatedSession sets active + clears pendingNewChat + refreshes", async () => {
    const service = makeService([]);
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(1));

    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("new", "auto-title")] });

    act(() => { result.current.promoteCreatedSession("new"); });
    expect(result.current.activeSessionId).toBe("new");
    expect(result.current.pendingNewChat).toBe(false);
    expect(onRouteChange).toHaveBeenCalledWith("new");
    await waitFor(() => {
      expect(service.listSessions).toHaveBeenCalledTimes(2);
      expect(result.current.sessions[0]?.title).toBe("auto-title");
    });
  });

  it("rename calls the service and refreshes", async () => {
    const service = makeService([s("a", "old")]);
    service.renameSession.mockResolvedValue(s("a", "new"));
    const { result } = renderHook(() => useSessions({ service, hydrate: makeHydrate(), onRouteChange: vi.fn() }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("a", "new")] });

    await act(async () => {
      await result.current.rename("a", "new");
    });
    expect(service.renameSession).toHaveBeenCalledWith("a", "new");
    await waitFor(() => expect(result.current.sessions[0].title).toBe("new"));
  });

  it("remove clears active + URL if deleting active session", async () => {
    const service = makeService([s("a"), s("b")]);
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(2));

    act(() => { result.current.setActiveImmediately("a"); });

    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("b")] });

    await act(async () => {
      await result.current.remove("a");
    });
    expect(service.deleteSession).toHaveBeenCalledWith("a");
    expect(result.current.activeSessionId).toBeNull();
    expect(onRouteChange).toHaveBeenLastCalledWith(null);
  });
});
