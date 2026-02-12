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
  it("renders correct number of steps for new_search", () => {
    const steps = makeSteps([
      { agentName: "entity", label: "Extracting entities", status: "complete" },
      { agentName: "parser", label: "Planning query", status: "complete" },
      { agentName: "api", label: "Building request", status: "active" },
      { agentName: "http", label: "Executing search", status: "pending" },
      { agentName: "chatter", label: "Summarizing results", status: "pending" },
    ]);

    render(<ProcessingStepper steps={steps} />);
    expect(screen.getByTitle("Extracting entities")).toBeInTheDocument();
    expect(screen.getByTitle("Building request")).toBeInTheDocument();
    expect(screen.getByTitle("Summarizing results")).toBeInTheDocument();
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
    // Check icon should be rendered (the text-green class indicates completion)
    expect(container.querySelector(".text-green-600")).toBeTruthy();
  });
});
