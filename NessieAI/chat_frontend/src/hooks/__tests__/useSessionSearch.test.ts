import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionSearch, SEARCH_DEBOUNCE_MS } from "../useSessionSearch";
import type { SessionSearchHit, SessionSearchResponse } from "@/lib/types/api";

const hit = (id: string): SessionSearchHit => ({
  source: "chat", session_id: id, title: id, updated_at: "2026-10-20T00:00:00Z", owner: null,
  turn_id: 1, matched_in: "question", snippet: "ndma", match_count: 1,
});
const resp = (ids: string[], terms = ["ndma"]): SessionSearchResponse => ({
  query: terms.join(" "), terms, sources: ["chats"], total: ids.length, results: ids.map(hit),
});

describe("useSessionSearch", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("does not search under two characters", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp([])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("n"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS * 2); });
    expect(service.searchSessions).not.toHaveBeenCalled();
    expect(result.current.active).toBe(false);
  });

  it("searches once, after the typing stops", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp(["a"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("nd"));
    act(() => result.current.setQuery("ndm"));
    act(() => result.current.setQuery("ndma"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(service.searchSessions).toHaveBeenCalledTimes(1);
    expect(service.searchSessions.mock.calls[0][0]).toBe("ndma");
    expect(result.current.results.map((h) => h.session_id)).toEqual(["a"]);
    expect(result.current.terms).toEqual(["ndma"]);
    expect(result.current.active).toBe(true);
  });

  it("drops an older answer that arrives after a newer one, and aborts it", async () => {
    let resolveOld!: (r: SessionSearchResponse) => void;
    const signals: AbortSignal[] = [];
    const service = {
      searchSessions: vi.fn((q: string, _o: unknown, signal?: AbortSignal) => {
        signals.push(signal!);
        return q === "old"
          ? new Promise<SessionSearchResponse>((r) => { resolveOld = r; })
          : Promise.resolve(resp(["new"], ["new"]));
      }),
    };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("old"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    act(() => result.current.setQuery("new"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(signals[0].aborted).toBe(true);
    await act(async () => { resolveOld(resp(["old"], ["old"])); });
    expect(result.current.results.map((h) => h.session_id)).toEqual(["new"]);
  });

  it("a filter change searches again with the filter", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp(["a"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("ndma"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    act(() => result.current.setFilters({ sinceDays: 30, allUsers: true }));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(service.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: 30, allUsers: true }, expect.any(AbortSignal));
  });

  it("clear empties the query and the results", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp(["a"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("ndma"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    act(() => result.current.clear());
    expect(result.current.query).toBe("");
    expect(result.current.results).toEqual([]);
    expect(result.current.active).toBe(false);
  });

  it("an error is shown and the next search still runs", async () => {
    const service = { searchSessions: vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(resp(["b"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("ndma"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(result.current.error).toBe("Search failed.");
    act(() => result.current.setQuery("ndma liver"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(result.current.error).toBeNull();
    expect(result.current.results.map((h) => h.session_id)).toEqual(["b"]);
  });
});
