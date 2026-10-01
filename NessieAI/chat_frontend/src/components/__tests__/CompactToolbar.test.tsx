import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CompactToolbar } from "../Layout/CompactToolbar";

function renderToolbar(overrides: Partial<Parameters<typeof CompactToolbar>[0]> = {}) {
  const props = {
    onRightToggle: vi.fn(),
    onLeftToggle: vi.fn(),
    onAboutOpen: vi.fn(),
    ...overrides,
  };
  render(<CompactToolbar {...props} />);
  return props;
}

describe("CompactToolbar", () => {
  it("opens the About page", () => {
    const props = renderToolbar();
    fireEvent.click(screen.getByRole("button", { name: "About Nessie" }));
    expect(props.onAboutOpen).toHaveBeenCalledTimes(1);
    expect(props.onRightToggle).not.toHaveBeenCalled();
    expect(props.onLeftToggle).not.toHaveBeenCalled();
  });

  it("keeps the chat list and Debug controls", () => {
    const props = renderToolbar();
    fireEvent.click(screen.getByLabelText("Toggle chat list"));
    fireEvent.click(screen.getByLabelText("Toggle debug panel"));
    expect(props.onLeftToggle).toHaveBeenCalledTimes(1);
    expect(props.onRightToggle).toHaveBeenCalledTimes(1);
  });
});
