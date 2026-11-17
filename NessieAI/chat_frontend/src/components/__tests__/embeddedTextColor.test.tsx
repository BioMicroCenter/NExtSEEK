// Typed text must stay visible in the embedded panel whatever the phone or browser does to colours.
// A user on iOS typed blind: the input had no colour of its own, so a dark-mode extension (or a host page
// declaring dark support) turned the typed text white on the panel's white background.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageInput } from "../ChatPanel/MessageInput";

const css = readFileSync(resolve(__dirname, "../../index.embedded.css"), "utf8").replace(/\s+/g, " ");

describe("embedded panel text colour", () => {
  it("declares the panel light-only, so browsers draw native controls light", () => {
    expect(css).toMatch(/#chat-assistant-root \{[^}]*color-scheme: light;/);
  });

  it("makes every form control take the panel's text colour, below any utility class", () => {
    expect(css).toMatch(
      /:where\(#chat-assistant-root\) :where\(input, textarea, select, button\) \{[^}]*color: inherit;[^}]*-webkit-text-fill-color: currentcolor;/,
    );
  });

  it("gives the chat input its own foreground colour", () => {
    render(<MessageInput onSend={vi.fn()} />);
    expect(screen.getByTestId("chat-input")).toHaveClass("text-foreground");
  });
});
