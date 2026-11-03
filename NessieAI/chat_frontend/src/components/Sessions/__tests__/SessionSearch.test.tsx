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
const resp = (results: SessionSearchHit[], facets = { record_type: { TIS: 3 }, project: { GBM: 2 }, where: { result: 3 } }):
  SessionSearchResponse => ({
  query: "ndma liver", terms: ["ndma", "liver"], sources: ["chats", "records"], total: results.length, results, facets,
});
const NONE = { steps: false, recordType: null, project: null, where: null, facets: false };
const OPEN = { ...NONE, facets: true }; // the options panel is open: the counts are asked for

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

  it("a result's title shows whole, never cut: a cut inside a mark paints the ellipsis yellow", async () => {
    setup();
    await type("ndma");
    const title = screen.getByTestId("session-search-result").querySelector("mark")!.parentElement!;
    expect(title).not.toHaveClass("truncate");
    expect(title).toHaveClass("break-words");
  });

  it("the box pads with pl/pr: the host page's Bootstrap px-3 is !important and would put text under the icon", () => {
    setup();
    const tokens = screen.getByRole("searchbox", { name: /search chats/i }).className.split(/\s+/);
    expect(tokens).toContain("pl-7");
    expect(tokens.filter((t) => /^px-/.test(t))).toEqual([]);
  });

  it("with the options panel closed, a search asks for no counts", async () => {
    const { searchService } = setup();
    await type("ndma");
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: null, allUsers: false, ...NONE }, expect.any(AbortSignal));
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
    expect(props.onOpenHit).toHaveBeenCalledWith("h1", 3, "chat");
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
    expect(screen.getByText("Chats, Records")).toBeInTheDocument();
    expect(screen.queryByLabelText(/all users/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/when/i), { target: { value: "30" } });
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: 30, allUsers: false, ...OPEN }, expect.any(AbortSignal));
    expect(options.querySelector("[data-filter-dot]")).not.toBeNull();
  });

  it("superusers get All users, and a hit shows its owner", async () => {
    const { searchService } = setup({ isAdmin: true });
    searchService.searchSessions.mockResolvedValue(resp([hit({ owner: "jdoe" })]));
    fireEvent.click(screen.getByRole("button", { name: /search options/i }));
    fireEvent.click(screen.getByLabelText(/all users/i));
    await type("ndma");
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: null, allUsers: true, ...OPEN }, expect.any(AbortSignal));
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

  it("Ctrl+Shift+K and Alt+K are left alone", () => {
    setup();
    const box = screen.getByRole("searchbox", { name: /search chats/i });
    fireEvent.keyDown(document, { key: "K", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(document, { key: "k", ctrlKey: true, altKey: true });
    act(() => { vi.runAllTimers(); });
    expect(box).not.toHaveFocus();
  });

  it("the box takes focus once per Ctrl+K, not again on a later remount", () => {
    const props = {
      sessions, activeSessionId: null, inFlight: false, onNewChat: vi.fn(), onSelect: vi.fn(),
      onRename: vi.fn(), onDelete: vi.fn(), onExpand: vi.fn(),
      searchService: { searchSessions: vi.fn().mockResolvedValue(resp([])) },
    };
    const { rerender } = render(<SessionSidebar {...props} collapsed={false} />);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    act(() => { vi.runAllTimers(); });
    const box = screen.getByRole("searchbox", { name: /search chats/i });
    expect(box).toHaveFocus();
    box.blur();
    rerender(<SessionSidebar {...props} collapsed={true} />);
    rerender(<SessionSidebar {...props} collapsed={false} />);
    act(() => { vi.runAllTimers(); });
    expect(screen.getByRole("searchbox", { name: /search chats/i })).not.toHaveFocus();
  });

  it("the box stops at 200 characters, the server's limit", () => {
    setup();
    expect(screen.getByRole("searchbox", { name: /search chats/i })).toHaveAttribute("maxLength", "200");
  });

  it("Agent steps is off until ticked, then searched", async () => {
    const { searchService } = setup();
    fireEvent.click(screen.getByRole("button", { name: /search options/i }));
    const steps = screen.getByLabelText(/agent steps/i);
    expect(steps).not.toBeChecked();
    fireEvent.click(steps);
    await type("ndma");
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "ndma", { sinceDays: null, allUsers: false, ...OPEN, steps: true }, expect.any(AbortSignal));
  });

  it("opening the panel loads the counts, and a record filter searches with an empty box", async () => {
    const { searchService } = setup();
    fireEvent.click(screen.getByRole("button", { name: /search options/i }));
    await act(async () => {});
    expect(searchService.searchSessions).toHaveBeenCalledWith("", { sinceDays: null, allUsers: false, facetsOnly: true });
    const type_ = screen.getByLabelText(/record type/i);
    expect([...type_.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Any", "TIS (3)"]);
    expect([...screen.getByLabelText(/project/i).querySelectorAll("option")].map((o) => o.textContent))
      .toEqual(["Any", "GBM (2)"]);
    fireEvent.change(type_, { target: { value: "TIS" } });
    await act(async () => { vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS); });
    expect(searchService.searchSessions).toHaveBeenLastCalledWith(
      "", { sinceDays: null, allUsers: false, ...OPEN, recordType: "TIS" }, expect.any(AbortSignal));
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    expect(screen.getByTestId("session-search-result")).toBeInTheDocument();
  });

  it("a record hit says so and links its sample IDs, not protocol IDs", async () => {
    const { searchService } = setup();
    searchService.searchSessions.mockResolvedValue(resp([hit({
      source: "record", matched_in: "record",
      snippet: "TIS-220831FLY-26, P.FLY-220630-V1_x.docx and 4 more",
    })]));
    await type("tis");
    expect(screen.getByText("in the records")).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/seek/sampletree/uid=TIS-220831FLY-26/"]);
  });

  it("an agent-steps hit says so", async () => {
    const { searchService } = setup();
    searchService.searchSessions.mockResolvedValue(resp([hit({ source: "steps", matched_in: "steps" })]));
    await type("schema");
    expect(screen.getByText("in the agent's steps")).toBeInTheDocument();
  });

  it("without a search service the sidebar has no search box", () => {
    setup({ searchService: undefined });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });
});
