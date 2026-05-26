import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionSidebar } from "../SessionSidebar";
import type { SessionListItem } from "@/lib/types/api";

const sessions: SessionListItem[] = [
  { session_id: "a", title: "Alpha", created_at: "", updated_at: "", query_count: 1, preview: "" },
  { session_id: "b", title: "Beta",  created_at: "", updated_at: "", query_count: 2, preview: "" },
];

const baseProps = {
  sessions,
  activeSessionId: null as string | null,
  collapsed: false,
  inFlight: false,
  onNewChat: vi.fn(),
  onSelect: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
};

describe("SessionSidebar", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(baseProps).forEach((v) => typeof v === "function" && (v as ReturnType<typeof vi.fn>).mockReset?.());
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("renders the list", () => {
    render(<SessionSidebar {...baseProps} />);
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });

  it("New chat button fires onNewChat", () => {
    const onNewChat = vi.fn();
    render(<SessionSidebar {...baseProps} onNewChat={onNewChat} />);
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("renders empty state when no sessions and not pending", () => {
    render(<SessionSidebar {...baseProps} sessions={[]} />);
    expect(screen.getByText(/start a new chat/i)).toBeInTheDocument();
  });

  it("clicking a row fires onSelect", () => {
    const onSelect = vi.fn();
    render(<SessionSidebar {...baseProps} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Alpha"));
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("inFlight disables row clicks (does not call onSelect)", () => {
    const onSelect = vi.fn();
    render(<SessionSidebar {...baseProps} inFlight={true} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Alpha"));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
