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
        onRightToggle={vi.fn()}
        onLeftToggle={vi.fn()}
        onAboutOpen={vi.fn()}
      />,
    );
    expect(screen.getByText("NExtSEEK Chat")).toBeInTheDocument();
  });

  it("calls onRightToggle when Debug button clicked", () => {
    const onRight = vi.fn();
    render(
      <HeaderBar
        onRightToggle={onRight}
        onLeftToggle={vi.fn()}
        onAboutOpen={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Toggle debug panel"));
    expect(onRight).toHaveBeenCalledTimes(1);
  });

  it("toggles dark mode class on click", () => {
    render(
      <HeaderBar
        onRightToggle={vi.fn()}
        onLeftToggle={vi.fn()}
        onAboutOpen={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Toggle dark mode"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("opens the About page, mirroring the shipped toolbar", () => {
    const onAbout = vi.fn();
    render(
      <HeaderBar
        onRightToggle={vi.fn()}
        onLeftToggle={vi.fn()}
        onAboutOpen={onAbout}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "About Nessie" }));
    expect(onAbout).toHaveBeenCalledTimes(1);
  });
});
