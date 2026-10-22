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

    expect(onSend).toHaveBeenCalledWith("test query", { pipeline: "standard" });
  });

  // PROD is no longer a MessageInput control — it moved to the Debug panel
  // (ProdToggle) as a sticky admin toggle, read at send time. See
  // ProdToggle.test.tsx and lib/useProd.

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    fireEvent.change(textarea, { target: { value: "test" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("is read-only, not disabled, while a turn runs, and does not send", () => {
    render(<MessageInput onSend={vi.fn()} disabled />);
    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    expect(textarea).not.toBeDisabled();
    expect(textarea).toHaveAttribute("readonly");
    expect(textarea).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(textarea, { key: "Enter" });
  });
});
