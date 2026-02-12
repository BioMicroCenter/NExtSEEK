import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatPanel } from "../ChatPanel/ChatPanel";
import type { Message, ProcessingState } from "@/lib/types/chat";

const defaultProcessing: ProcessingState = {
  isProcessing: false,
  steps: [],
  currentStepIndex: -1,
  mode: null,
};

const defaultProps = {
  messages: [] as Message[],
  processingState: defaultProcessing,
  isDisabled: false,
  onSendMessage: vi.fn(),
};

describe("ChatPanel", () => {
  it("renders empty state message", () => {
    render(<ChatPanel {...defaultProps} />);
    expect(
      screen.getByText("Ask NExtSEEK a question to get started"),
    ).toBeInTheDocument();
  });

  it("disables input when isDisabled is true", () => {
    render(<ChatPanel {...defaultProps} isDisabled={true} />);
    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    expect(textarea).toBeDisabled();
  });

  it("enables input when isDisabled is false", () => {
    render(<ChatPanel {...defaultProps} isDisabled={false} />);
    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    expect(textarea).not.toBeDisabled();
  });

  it("shows stepper when processing", () => {
    const processing: ProcessingState = {
      isProcessing: true,
      steps: [
        {
          index: 0,
          label: "Extracting entities",
          agentName: "entity",
          status: "active",
        },
      ],
      currentStepIndex: 0,
      mode: null,
    };

    render(<ChatPanel {...defaultProps} processingState={processing} />);
    expect(screen.getByTitle("Extracting entities")).toBeInTheDocument();
  });
});
