import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { AboutDialog } from "../Layout/AboutDialog";

const HERE = dirname(fileURLToPath(import.meta.url));

function section(name: string): HTMLElement {
  return screen.getByRole("region", { name });
}

describe("AboutDialog", () => {
  it("renders nothing while closed", () => {
    render(<AboutDialog open={false} onOpenChange={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("has the four sections, in order", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "About Nessie" });
    const headings = within(dialog)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(headings).toEqual([
      "What Nessie is",
      "The data model",
      "Downloading data",
      "How long an answer takes",
    ]);
  });

  it("names every part of the data model", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const text = section("The data model").textContent ?? "";
    for (const term of ["Sample", "Sample type", "Attribute", "Assay", "Project", "Lineage"]) {
      expect(text).toContain(term);
    }
  });

  it("says where each kind of download lives", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const text = section("Downloading data").textContent ?? "";
    // The button labels a user will actually see.
    expect(text).toContain("Download All Tables (.xlsx)");
    expect(text).toContain("Download samples");
    // The raw files are not sent by Nessie: the record names where they are.
    expect(text).toContain("Link_PrimaryData");
    expect(text).toContain("Checksum_PrimaryData");
  });

  it("states the three-minute stop instead of an open-ended wait", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const text = section("How long an answer takes").textContent ?? "";
    expect(text).toContain("three minutes");
    // The words a user sees when the stop fires, so they can recognise it, and what to do.
    expect(text).toContain("took longer than the 3-minute limit");
    expect(text).toContain("Say continue and it carries on from where it got to.");
    expect(text).not.toContain("exceeded the 180s limit");
    // Operator decision D2: the ceiling stays, so the copy must not promise
    // an unbounded run.
    expect(text).not.toMatch(/claude code/i);
    expect(text).not.toMatch(/can take a while/i);
  });

  it("says what happens when the AI model is unavailable", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const text = section("How long an answer takes").textContent ?? "";
    expect(text).toContain(
      "If the AI model is unavailable, Nessie tries a second one. If that fails too, the chat says so; ask again in a few minutes.",
    );
  });

  it("does not promise that every search ends within a minute", () => {
    render(<AboutDialog open onOpenChange={vi.fn()} />);
    const text = section("How long an answer takes").textContent ?? "";
    // Only a step-by-step task has a ceiling. A search has none, and one that
    // gathers a whole lineage runs for minutes, so the copy has to say so.
    expect(text).toContain("under a minute");
    expect(text).toContain("several minutes");
  });

  it("closes through onOpenChange", () => {
    const onOpenChange = vi.fn();
    render(<AboutDialog open onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

// The timing section promises a three-minute stop. That is true only while the
// server's hard ceiling is 180 s (NessieAI/cc/cc_engine.py). If someone raises
// the default, this fails so the copy is rewritten in the same change.
describe("the three-minute copy matches the server's ceiling", () => {
  it("NEXTSEEK_CC_TIMEOUT_HARD_MAX still defaults to 180 s", () => {
    const engine = readFileSync(resolve(HERE, "../../../../cc/cc_engine.py"), "utf8");
    const match = engine.match(/"NEXTSEEK_CC_TIMEOUT_HARD_MAX",\s*"(\d+)"/);
    expect(match, "the hard-max default moved or was renamed").not.toBeNull();
    expect(match?.[1]).toBe("180");
  });
});
