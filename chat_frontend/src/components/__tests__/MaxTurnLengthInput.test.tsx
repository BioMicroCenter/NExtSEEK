import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MaxTurnLengthInput } from "../Layout/MaxTurnLengthInput";
import { getMaxTurnLength } from "@/lib/maxTurnLength";

const LABEL = "Max Container-CC turn length in seconds";

describe("MaxTurnLengthInput", () => {
  beforeEach(() => localStorage.clear());

  it("renders nothing for non-admins", () => {
    const { container } = render(<MaxTurnLengthInput />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders for admins, empty by default (server default)", () => {
    render(<MaxTurnLengthInput isAdmin />);
    const input = screen.getByLabelText(LABEL) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("");
  });

  it("persists a value to localStorage", () => {
    render(<MaxTurnLengthInput isAdmin />);
    const input = screen.getByLabelText(LABEL) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "300" } });
    expect(getMaxTurnLength()).toBe(300);
  });

  it("initializes from and clears the persisted override", () => {
    localStorage.setItem("nextseek.maxTurnLength", "300");
    render(<MaxTurnLengthInput isAdmin />);
    const input = screen.getByLabelText(LABEL) as HTMLInputElement;
    expect(input.value).toBe("300");
    fireEvent.change(input, { target: { value: "" } });
    expect(getMaxTurnLength()).toBeNull();
  });
});
