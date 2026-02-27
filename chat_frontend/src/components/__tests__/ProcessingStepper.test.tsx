import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
  it("renders steps auto-expanded", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "complete" },
      { agentName: "parser", label: "Planning query", status: "complete" },
      { agentName: "api", label: "Building request", status: "active" },
      { agentName: "http", label: "Executing search", status: "pending" },
      { agentName: "chatter", label: "Summarizing results", status: "pending" },
    ]);

    render(<ProcessingStepper steps={steps} />);
    // Expanded by default — all steps visible in the expanded list
    expect(screen.getAllByText("Extracting entities").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Summarizing results").length).toBeGreaterThanOrEqual(1);
  });

  it("shows active step with spinner", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "active" },
    ]);

    const { container } = render(<ProcessingStepper steps={steps} />);
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("shows completed step with check icon when expanded", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "complete" },
    ]);

    const { container } = render(<ProcessingStepper steps={steps} />);
    // Auto-expanded — green check should be visible immediately
    expect(container.querySelector(".text-green-600")).toBeTruthy();
  });
});
