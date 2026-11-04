import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DebugPanel } from "@/components/DebugPanel/DebugPanel";
import type { DebugData } from "@/lib/types/chat";

const data = (...entries: Array<[string, string]>): DebugData => ({
  entries: entries.map(([agent, summary]) => ({ agent, summary, timestamp: new Date(0) })),
  bundleId: null,
  query: "q",
});

describe("DebugPanel", () => {
  it("shows the router's route line without opening the entry", () => {
    render(
      <DebugPanel
        debugData={data(["router", "→ container_cc  ·  source=forced  ·  pinned CC: applied\nreasoning-line-for-the-open-entry"])}
      />,
    );
    const entry = screen.getByTestId("debug-entry");
    // The accordion is closed: only its trigger is on screen, and it carries the first line.
    expect(entry).toHaveTextContent("→ container_cc · source=forced · pinned CC: applied");
    // The reasoning (second line) stays inside the closed entry.
    expect(entry).not.toHaveTextContent("reasoning-line-for-the-open-entry");
  });

  it("leaves other agents' triggers as the badge and the time", () => {
    render(<DebugPanel debugData={data(["entity", "Found: TIS"])} />);
    expect(screen.getByTestId("debug-entry")).not.toHaveTextContent("Found: TIS");
  });
});
