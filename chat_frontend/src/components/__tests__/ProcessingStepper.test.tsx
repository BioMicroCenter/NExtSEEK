import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProcessingStepper } from "../ChatPanel/ProcessingStepper";
import type { Step } from "@/lib/types/chat";

function makeSteps(
  configs: { agentName: string; label: string; status: Step["status"] }[],
): Step[] {
  return configs.map((c, i) => ({
    index: i,
    label: c.label,
    agentName: c.agentName,
    status: c.status,
  }));
}

describe("ProcessingStepper", () => {
  it("renders correct number of steps for new_search", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "complete" },
      { agentName: "parser", label: "Planning query", status: "complete" },
      { agentName: "api", label: "Building request", status: "active" },
      { agentName: "http", label: "Executing search", status: "pending" },
      { agentName: "chatter", label: "Summarizing results", status: "pending" },
    ]);

    render(<ProcessingStepper steps={steps} />);
    // Collapsed banner shows the active step label
    expect(screen.getByText(/Building request/)).toBeInTheDocument();
    // Expand to see all steps
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Extracting entities")).toBeInTheDocument();
    expect(screen.getByText("Summarizing results")).toBeInTheDocument();
  });

  it("shows active step with spinner", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "active" },
    ]);

    const { container } = render(<ProcessingStepper steps={steps} />);
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("shows completed step with check icon", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "complete" },
    ]);

    const { container } = render(<ProcessingStepper steps={steps} />);
    // Expand to reveal completed steps with green check
    fireEvent.click(screen.getByRole("button"));
    expect(container.querySelector(".text-green-600")).toBeTruthy();
  });
});
