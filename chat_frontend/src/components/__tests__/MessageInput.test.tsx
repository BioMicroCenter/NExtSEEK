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

    expect(onSend).toHaveBeenCalledWith("test query", { pipeline: "standard", useProd: false });
  });

  it("hides PROD checkbox for non-admin users", () => {
    render(<MessageInput onSend={vi.fn()} />);
    expect(screen.queryByLabelText("Use prod database")).not.toBeInTheDocument();
  });

  it("shows PROD checkbox for admin users", () => {
    render(<MessageInput onSend={vi.fn()} isAdmin />);
    expect(screen.getByLabelText("Use prod database")).toBeInTheDocument();
  });

  it("sends useProd=true when admin checks PROD and submits", () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} isAdmin />);

    const checkbox = screen.getByLabelText("Use prod database") as HTMLInputElement;
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);

    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    fireEvent.change(textarea, { target: { value: "prod query" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("prod query", { pipeline: "standard", useProd: true });
  });

  it("switches pipeline to plan when Planner button clicked", () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} />);

    fireEvent.click(screen.getByRole("button", { name: "Planner" }));

    const textarea = screen.getByPlaceholderText("Ask NExtSEEK a question...");
    fireEvent.change(textarea, { target: { value: "planner query" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("planner query", { pipeline: "plan", useProd: false });
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
