import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { SessionSidebar } from "../SessionSidebar";
import { SEARCH_DEBOUNCE_MS } from "@/hooks/useSessionSearch";
import type { SessionListItem, SessionSearchHit, SessionSearchResponse } from "@/lib/types/api";

const sessions: SessionListItem[] = [
  { session_id: "a", title: "Alpha", created_at: "", updated_at: "", query_count: 1, preview: "" },
];

const hit = (over: Partial<SessionSearchHit> = {}): SessionSearchHit => ({
  source: "chat", session_id: "h1", title: "NDMA mice", updated_at: "2026-10-21T14:00:00Z", owner: null,
  turn_id: 3, matched_in: "question", snippet: "find mice treated with NDMA in the liver", match_count: 1, ...over,
});
const resp = (results: SessionSearchHit[]): SessionSearchResponse => ({
  query: "ndma liver", terms: ["ndma", "liver"], sources: ["chats"], total: results.length, results,
});

function setup(over: Record<string, unknown> = {}) {
  const searchService = { searchSessions: vi.fn().mockResolvedValue(resp([hit()])) };
  const props = {
    sessions, activeSessionId: null, collapsed: false, inFlight: false,
    onNewChat: vi.fn(), onSelect: vi.fn(), onRename: vi.fn(), onDelete: vi.fn(),
    searchService, onOpenHit: vi.fn(), onExpand: vi.fn(), isAdmin: false, ...over,
  };
  render(<SessionSidebar {...props} />);
  return { props, searchService };
}

async function type(text: string) {
  fireEvent.change(screen.getByRole("searchbox", { name: /search chats/i }), { target: { value: text } });
  await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
}

describe("SessionSidebar search", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("typing replaces the list with results, the terms marked", async () => {
    setup();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    await type("ndma liver");
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    const result = screen.getByTestId("session-search-result");
    expect(result).toHaveTextContent("NDMA mice");
    expect([...result.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["NDMA", "NDMA", "liver"]); // title, then snippet
  });

  it("says when nothing matches", async () => {
    const { searchService } = setup();
    searchService.searchSessions.mockResolvedValue(resp([]));
    await type("zzzz");
    expect(screen.getByText("No chats match")).toBeInTheDocument();
  });

  it("Escape clears the box and brings the list back", async () => {
    setup();
    await type("ndma");
    fireEvent.keyDown(screen.getByRole("searchbox", { name: /search chats/i }), { key: "Escape" });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: /search chats/i })).toHaveValue("");
  });

  it("a click opens the chat at the matching turn", async () => {
    const { props } = setup();
    await type("ndma");
    fireEvent.click(screen.getByTestId("session-search-result"));
    expect(props.onOpenHit).toHaveBeenCalledWith("h1", 3);
  });

  it("results do nothing while a turn is running", async () => {
    const { props } = setup({ inFlight: true });
    await type("ndma");
    fireEvent.click(screen.getByTestId("session-search-result"));
    expect(props.onOpenHit).not.toHaveBeenCalled();
  });

  it("the options panel filters by date and shows a dot when a filter is set", async () => {
    const { searchService } = setup();
    await type("ndma");
    const options = screen.getByRole("button", { name: /search options/i });
    expect(options.querySelector("[data-filter-dot]")).toBeNull();
    fireEvent.click(options);
    expect(screen.getByText("Chats")).toBeInTheDocument();
    expect(screen.queryByLabelText(/all users/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/when/i), { target: { value: "30" } });
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: 30, allUsers: false }, expect.any(AbortSignal));
    expect(options.querySelector("[data-filter-dot]")).not.toBeNull();
  });

  it("superusers get All users, and a hit shows its owner", async () => {
    const { searchService } = setup({ isAdmin: true });
    searchService.searchSessions.mockResolvedValue(resp([hit({ owner: "jdoe" })]));
    fireEvent.click(screen.getByRole("button", { name: /search options/i }));
    fireEvent.click(screen.getByLabelText(/all users/i));
    await type("ndma");
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: null, allUsers: true }, expect.any(AbortSignal));
    expect(screen.getByTestId("session-search-result")).toHaveTextContent("jdoe");
  });

  it.each([{ ctrlKey: true }, { metaKey: true }])("Ctrl/Cmd+K focuses the box (%o)", (mod) => {
    setup();
    fireEvent.keyDown(document, { key: "k", ...mod });
    act(() => { vi.runAllTimers(); });
    expect(screen.getByRole("searchbox", { name: /search chats/i })).toHaveFocus();
  });

  it("Ctrl+K on a collapsed sidebar asks to expand it; the icon does too", () => {
    const { props } = setup({ collapsed: true });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(props.onExpand).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /search chats/i }));
    expect(props.onExpand).toHaveBeenCalledTimes(2);
  });

  it("without a search service the sidebar has no search box", () => {
    setup({ searchService: undefined });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });
});
