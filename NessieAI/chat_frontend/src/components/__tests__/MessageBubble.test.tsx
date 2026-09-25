import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageBubble } from "../ChatPanel/MessageBubble";
import type { Message, Suggestion } from "@/lib/types/chat";

function makeMsg(overrides: Partial<Message>): Message {
  return {
    id: "1",
    content: "test",
    isUser: false,
    timestamp: new Date(),
    status: "sent",
    messageType: "text",
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders user messages right-aligned", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ isUser: true, content: "hello" })} />,
    );
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(container.querySelector(".items-end")).toBeTruthy();
  });

  it("renders assistant messages left-aligned", () => {
    const { container } = render(
      <MessageBubble
        message={makeMsg({ isUser: false, content: "response" })}
      />,
    );
    expect(screen.getByText("response")).toBeInTheDocument();
    expect(container.querySelector(".items-start")).toBeTruthy();
  });

  it("renders system messages centered", () => {
    const { container } = render(
      <MessageBubble
        message={makeMsg({ messageType: "system", content: "notice" })}
      />,
    );
    expect(screen.getByText("notice")).toBeInTheDocument();
    expect(container.querySelector(".justify-center")).toBeTruthy();
  });

  it("shows a system message's artifacts under its text, downloading through the CC route", () => {
    const onCc = vi.fn();
    const onNative = vi.fn();
    render(
      <MessageBubble
        message={makeMsg({
          messageType: "system",
          content: "Error: the turn timed out",
          mode: "cc",
          artifacts: [
            { artifact_type: "file", key: "run-1/report.csv", label: "report.csv", file_format: "csv" },
          ],
        })}
        onArtifactDownload={onNative}
        onCcArtifactDownload={onCc}
      />,
    );
    expect(screen.getByText("Error: the turn timed out")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("artifact-download"));
    expect(onCc).toHaveBeenCalledWith("run-1/report.csv");
    expect(onNative).not.toHaveBeenCalled();
  });

  it("routes CC artifact download through onCcArtifactDownload when mode is cc", () => {
    const onCc = vi.fn();
    const onNative = vi.fn();
    render(
      <MessageBubble
        message={makeMsg({
          mode: "cc",
          bundleId: 0,
          artifacts: [
            { artifact_type: "file", key: "report.md", label: "Report", file_format: "md" },
          ],
        })}
        onArtifactDownload={onNative}
        onCcArtifactDownload={onCc}
      />,
    );
    fireEvent.click(screen.getByTestId("artifact-download"));
    expect(onCc).toHaveBeenCalledWith("report.md");
    expect(onNative).not.toHaveBeenCalled();
  });
});

// The graph-result reviewer's chips (#128) ride on the NS turn's debug.suggestions.
// A chip's query is a whole question: one click sends it as the next message.
const CONVERTER: Suggestion = {
  id: "b7-r0",
  source: "reviewer",
  kind: "split",
  label: "Only Converter",
  query: "Show samples for human subjects classified as Converter.",
  reason: "57 of 98 were Non-converter.",
};
const NON_CONVERTER: Suggestion = {
  id: "b7-r1",
  source: "reviewer",
  kind: "split",
  label: "Only Non-converter",
  query: "Show samples for human subjects classified as Non-converter.",
  reason: "41 of 98 were Converter.",
};
const assistantMsg = makeMsg({ content: "Found 98 samples for human subjects." });

describe("MessageBubble suggestion chips", () => {
  it("renders a chip for each suggestion on the last assistant message and sends its query", () => {
    const onSuggestion = vi.fn();
    render(
      <MessageBubble
        message={{ ...assistantMsg, suggestions: [CONVERTER, NON_CONVERTER] }}
        isLast
        onSuggestion={onSuggestion}
      />,
    );
    const chips = screen.getAllByTestId("suggestion-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Only Converter");
    expect(chips[0]).toHaveAttribute("title", "57 of 98 were Non-converter.");
    expect(chips[0]).toHaveAttribute("data-source", "reviewer");
    expect(chips[0]).toHaveAttribute("data-suggestion-id", "b7-r0");
    expect(chips[0]).toBeEnabled();

    fireEvent.click(chips[0]);
    expect(onSuggestion).toHaveBeenCalledTimes(1);
    expect(onSuggestion).toHaveBeenCalledWith("Show samples for human subjects classified as Converter.");

    fireEvent.click(chips[1]);
    expect(onSuggestion).toHaveBeenLastCalledWith("Show samples for human subjects classified as Non-converter.");
  });

  it("disables chips mid-turn and hides them on older messages", () => {
    const onSuggestion = vi.fn();
    const { rerender } = render(
      <MessageBubble message={{ ...assistantMsg, suggestions: [CONVERTER] }} isLast disabled onSuggestion={onSuggestion} />,
    );
    const chip = screen.getByTestId("suggestion-chip");
    expect(chip).toBeDisabled();
    fireEvent.click(chip);
    expect(onSuggestion).not.toHaveBeenCalled();

    rerender(
      <MessageBubble message={{ ...assistantMsg, suggestions: [CONVERTER] }} isLast={false} onSuggestion={onSuggestion} />,
    );
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();
  });

  it("renders nothing when there are no suggestions", () => {
    const onSuggestion = vi.fn();
    const { rerender } = render(<MessageBubble message={assistantMsg} isLast onSuggestion={onSuggestion} />);
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();

    rerender(<MessageBubble message={{ ...assistantMsg, suggestions: [] }} isLast onSuggestion={onSuggestion} />);
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();
  });

  it("renders the label and the reason as plain text, never as markup", () => {
    const hostile: Suggestion = {
      ...CONVERTER,
      label: "<b>Only</b> Converter",
      reason: '<img src="x" onerror="alert(1)">',
    };
    render(<MessageBubble message={{ ...assistantMsg, suggestions: [hostile] }} isLast onSuggestion={vi.fn()} />);
    const chip = screen.getByTestId("suggestion-chip");
    expect(chip).toHaveTextContent("<b>Only</b> Converter");
    expect(chip.querySelector("b")).toBeNull();
    expect(chip).toHaveAttribute("title", '<img src="x" onerror="alert(1)">');
    expect(document.querySelector("img")).toBeNull();
  });
});
