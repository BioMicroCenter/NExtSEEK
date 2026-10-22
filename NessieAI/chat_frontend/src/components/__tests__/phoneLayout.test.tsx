import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageInput } from "../ChatPanel/MessageInput";
import { ChatPanel } from "../ChatPanel/ChatPanel";
import { SessionSidebar } from "../Sessions/SessionSidebar";
import { CompactToolbar } from "../Layout/CompactToolbar";

/** matchMedia where only the listed queries match. */
function media(matching: (q: string) => boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: matching(q),
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}
afterEach(() => vi.unstubAllGlobals());

const session = { session_id: "s1", title: "First", updated_at: "2026-01-01T00:00:00Z" } as never;
const sidebarProps = {
  sessions: [session],
  activeSessionId: null,
  collapsed: false,
  inFlight: false,
  onNewChat: vi.fn(),
  onSelect: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
};

describe("composer focus", () => {
  it("takes focus when a turn starts and ends on a fine pointer", () => {
    media((q) => q === "(pointer: fine)");
    const { rerender } = render(<MessageInput onSend={vi.fn()} disabled={false} />);
    const ta = screen.getByTestId("chat-input");
    expect(ta).not.toHaveFocus();
    rerender(<MessageInput onSend={vi.fn()} disabled />);
    expect(ta).toHaveFocus();
    ta.blur();
    rerender(<MessageInput onSend={vi.fn()} disabled={false} />);
    expect(ta).toHaveFocus();
  });

  it("never takes focus on a coarse pointer", () => {
    media(() => false);
    const { rerender } = render(<MessageInput onSend={vi.fn()} disabled={false} />);
    rerender(<MessageInput onSend={vi.fn()} disabled />);
    rerender(<MessageInput onSend={vi.fn()} disabled={false} />);
    expect(screen.getByTestId("chat-input")).not.toHaveFocus();
  });
});

describe("saved-chats rail or sheet", () => {
  it("is a rail from 768px up", () => {
    media(() => false);
    render(<SessionSidebar {...sidebarProps} />);
    expect(screen.getByRole("complementary", { name: "Saved chats" })).toBeInTheDocument();
  });

  it("is a closed sheet on a phone, and closes after a pick", () => {
    media((q) => q.includes("max-width: 767.98px"));
    const onSheetOpenChange = vi.fn();
    const onSelect = vi.fn();
    const { rerender } = render(<SessionSidebar {...sidebarProps} onSelect={onSelect} onSheetOpenChange={onSheetOpenChange} />);
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByTestId("session-item")).toBeNull();
    rerender(<SessionSidebar {...sidebarProps} onSelect={onSelect} sheetOpen onSheetOpenChange={onSheetOpenChange} />);
    fireEvent.click(screen.getByText("First"));
    expect(onSelect).toHaveBeenCalledWith("s1");
    expect(onSheetOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("toolbar and status", () => {
  it("the Menu button calls the site's openSidebar", () => {
    media(() => false);
    const open = vi.fn();
    (window as unknown as { openSidebar: () => void }).openSidebar = open;
    render(<CompactToolbar onRightToggle={vi.fn()} onLeftToggle={vi.fn()} onAboutOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Site menu", hidden: true }));
    expect(open).toHaveBeenCalled();
  });

  it("the message list is a log and the status line says Answer ready", () => {
    media(() => false);
    const msgs = [{ id: "1", content: "hi", isUser: false, messageType: "text", timestamp: new Date() }] as never;
    render(
      <ChatPanel
        messages={msgs}
        processingState={{ isProcessing: false, steps: [] } as never}
        isDisabled={false}
        onSendMessage={vi.fn()}
      />,
    );
    expect(screen.getByRole("log")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Answer ready");
  });
});
