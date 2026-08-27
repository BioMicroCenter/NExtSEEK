import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageBubble } from "../ChatPanel/MessageBubble";
import type { Message } from "@/lib/types/chat";

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
