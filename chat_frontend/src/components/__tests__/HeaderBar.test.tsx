import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { HeaderBar } from "../Layout/HeaderBar";

// Mock localStorage and matchMedia
beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
  });
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  document.documentElement.classList.remove("dark");
});

describe("HeaderBar", () => {
  it("renders the title 'NExtSEEK Chat'", () => {
    render(
      <HeaderBar
        onLeftToggle={vi.fn()}
      />,
    );
    expect(screen.getByText("NExtSEEK Chat")).toBeInTheDocument();
  });

  it("calls onLeftToggle when Tests button clicked", () => {
    const onLeft = vi.fn();
    render(
      <HeaderBar
        onLeftToggle={onLeft}
      />,
    );

    fireEvent.click(screen.getByLabelText("Toggle tests panel"));
    expect(onLeft).toHaveBeenCalledTimes(1);
  });

  it("toggles dark mode class on click", () => {
    render(
      <HeaderBar
        onLeftToggle={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Toggle dark mode"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
