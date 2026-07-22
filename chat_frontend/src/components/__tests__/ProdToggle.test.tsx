import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProdToggle } from "../Layout/ProdToggle";
import { getUseProd } from "@/lib/useProd";

describe("ProdToggle", () => {
  beforeEach(() => localStorage.clear());

  it("renders nothing for non-admins", () => {
    const { container } = render(<ProdToggle />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders for admins and defaults to DEV (unchecked)", () => {
    render(<ProdToggle isAdmin />);
    const cb = screen.getByLabelText("Query PROD database") as HTMLInputElement;
    expect(cb).toBeInTheDocument();
    expect(cb.checked).toBe(false);
    expect(screen.getByText("DEV")).toBeInTheDocument();
  });

  it("persists the choice to localStorage (the shell reads it at send time)", () => {
    render(<ProdToggle isAdmin />);
    const cb = screen.getByLabelText("Query PROD database") as HTMLInputElement;
    fireEvent.click(cb);
    expect(cb.checked).toBe(true);
    expect(getUseProd()).toBe(true);
    expect(screen.getByText("PROD")).toBeInTheDocument();
  });

  it("initializes from the persisted value", () => {
    localStorage.setItem("nextseek.useProd", "1");
    render(<ProdToggle isAdmin />);
    const cb = screen.getByLabelText("Query PROD database") as HTMLInputElement;
    expect(cb.checked).toBe(true);
  });
});
