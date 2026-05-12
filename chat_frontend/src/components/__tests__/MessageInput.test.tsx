import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageInput } from "../ChatPanel/MessageInput";

describe("MessageInput", () => {
  it("renders a textarea", () => {
    render(<MessageInput onSend={vi.fn()} />);
    expect(
      screen.getByPlaceholderText("Ask NExtSEEK a question..."),
    ).toBeInTheDocument();
  });

  it("sends message on Enter key", () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    fireEvent.change(textarea, { target: { value: "test query" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("test query", "standard");
  });

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    fireEvent.change(textarea, { target: { value: "test" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("is disabled when disabled prop is true", () => {
    render(<MessageInput onSend={vi.fn()} disabled />);
    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    expect(textarea).toBeDisabled();
  });
});
