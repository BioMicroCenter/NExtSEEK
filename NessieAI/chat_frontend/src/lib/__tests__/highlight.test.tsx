import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { highlightTerms } from "../highlight";

const marks = (text: string, terms: string[]) => {
  const { container } = render(<p>{highlightTerms(text, terms)}</p>);
  return { text: container.textContent, marked: [...container.querySelectorAll("mark")].map((m) => m.textContent) };
};

describe("highlightTerms", () => {
  it("marks each term where a word starts, any case, keeping the text whole", () => {
    expect(marks("Find NDMA mice in the liver; RNA-seq", ["ndma", "seq"]))
      .toEqual({ text: "Find NDMA mice in the liver; RNA-seq", marked: ["NDMA", "seq"] });
  });

  it("never marks mid-word, and treats regex characters literally", () => {
    expect(marks("address D.SEQ-1 DXSEQ-1", ["dress", "d.seq"]).marked).toEqual(["D.SEQ"]);
  });

  it("is Unicode-aware and prefers the longer of two overlapping terms", () => {
    expect(marks("a naïve ndma test", ["ve", "nd", "ndma"]).marked).toEqual(["ndma"]);
  });

  it("builds no lookbehind (Safari 16.0-16.3 throws on one at render)", async () => {
    const src = (await import("../highlight?raw")).default as string;
    expect(src).not.toContain("(?<");
  });

  it("returns the text unchanged without terms", () => {
    expect(marks("plain", [])).toEqual({ text: "plain", marked: [] });
  });
});
