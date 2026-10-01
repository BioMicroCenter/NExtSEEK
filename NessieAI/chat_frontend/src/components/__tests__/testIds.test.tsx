import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightSidebar } from "@/components/Layout/RightSidebar";
import { DebugPanel } from "@/components/DebugPanel/DebugPanel";
import { SessionListItem } from "@/components/Sessions/SessionListItem";
import { MessageBubble } from "@/components/ChatPanel/MessageBubble";
import type { DebugData, Message } from "@/lib/types/chat";

const noBundle: DebugData = { entries: [], bundleId: null, query: "" };
const withEntry: DebugData = {
  entries: [{ agent: "router", summary: "nextseek_query (baml)", timestamp: new Date() }],
  bundleId: 3,
  query: "What can you do?",
};

describe("test ids the Nessie CI lane relies on", () => {
  it("download buttons exist and are disabled without a bundle", () => {
    render(<RightSidebar isOpen onOpenChange={() => {}} debugData={noBundle} onDownload={() => {}} />);
    expect(screen.getByTestId("json-download")).toBeDisabled();
    expect(screen.getByTestId("metadata-download")).toBeDisabled();
  });

  it("the debug panel and each entry are addressable", () => {
    render(<DebugPanel debugData={withEntry} />);
    expect(screen.getByTestId("debug-panel")).toBeInTheDocument();
    expect(screen.getByTestId("debug-entry")).toHaveAttribute("data-agent", "router");
  });

  it("the empty debug panel is addressable too", () => {
    render(<DebugPanel debugData={noBundle} />);
    expect(screen.getByTestId("debug-panel")).toBeInTheDocument();
  });

  it("each saved-chat row carries its session id", () => {
    const item = {
      session_id: "uuid-1",
      title: "Hello world",
      created_at: "2026-05-12T00:00:00Z",
      updated_at: "2026-05-12T00:00:00Z",
      query_count: 3,
      preview: "first query",
    };
    render(
      <SessionListItem item={item} active={false} disabled={false}
        onSelect={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.getByTestId("session-item")).toHaveAttribute("data-session-id", "uuid-1");
  });

  it("each suggestion chip carries its id and source", () => {
    const message: Message = {
      id: "a-1",
      content: "Found 98 samples.",
      isUser: false,
      timestamp: new Date(),
      status: "sent",
      messageType: "text",
      suggestions: [
        {
          id: "b7-r0",
          source: "reviewer",
          label: "Only Converter",
          query: "Show samples for human subjects classified as Converter.",
          reason: "57 of 98 were Non-converter.",
        },
      ],
    };
    render(<MessageBubble message={message} isLast onSuggestion={vi.fn()} />);
    const chip = screen.getByTestId("suggestion-chip");
    expect(chip).toHaveAttribute("data-suggestion-id", "b7-r0");
    expect(chip).toHaveAttribute("data-source", "reviewer");
  });
});
