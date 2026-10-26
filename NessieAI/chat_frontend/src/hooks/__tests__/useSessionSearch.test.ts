import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionSearch, SEARCH_DEBOUNCE_MS } from "../useSessionSearch";
import type { SessionSearchHit, SessionSearchResponse } from "@/lib/types/api";

const hit = (id: string): SessionSearchHit => ({
  source: "chat", session_id: id, title: id, updated_at: "2026-10-20T00:00:00Z", owner: null,
  turn_id: 1, matched_in: "question", snippet: "ndma", match_count: 1,
});
const resp = (ids: string[], terms = ["ndma"]): SessionSearchResponse => ({
  query: terms.join(" "), terms, sources: ["chats", "records"], total: ids.length, results: ids.map(hit),
  facets: { record_type: { TIS: ids.length }, project: {}, where: {} },
});
const NONE = { steps: false, recordType: null, project: null, where: null };

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
    act(() => result.current.setFilters({ ...result.current.filters, sinceDays: 30, allUsers: true }));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(service.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: 30, allUsers: true, ...NONE }, expect.any(AbortSignal));
  });

  it("shows loading, not an empty result, from the first keystroke", () => {
    const service = { searchSessions: vi.fn().mockReturnValue(new Promise(() => {})) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setQuery("ndma"));
    expect(result.current.loading).toBe(true);
  });

  it("a record filter alone searches, with no words", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp(["a"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setFilters({ ...result.current.filters, recordType: "TIS" }));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(result.current.active).toBe(true);
    expect(service.searchSessions).toHaveBeenLastCalledWith(
      "", { sinceDays: null, allUsers: false, ...NONE, recordType: "TIS" }, expect.any(AbortSignal));
    expect(result.current.facets.record_type).toEqual({ TIS: 1 });
  });

  it("one letter with a filter searches by the filter only", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue(resp(["a"])) };
    const { result } = renderHook(() => useSessionSearch(service));
    act(() => result.current.setFilters({ ...result.current.filters, project: "GBM" }));
    act(() => result.current.setQuery("n"));
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(service.searchSessions.mock.calls.at(-1)?.[0]).toBe("");
  });

  it("loadFacets asks for the counts only, over the date and user scope", async () => {
    const service = { searchSessions: vi.fn().mockResolvedValue({ ...resp([]), facets: { record_type: { MUS: 4 }, project: {}, where: {} } }) };
    const { result } = renderHook(() => useSessionSearch(service));
    await act(async () => { result.current.loadFacets(); });
    expect(service.searchSessions).toHaveBeenLastCalledWith(
      "", { sinceDays: null, allUsers: false, facetsOnly: true });
    expect(result.current.facets.record_type).toEqual({ MUS: 4 });
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
