import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "../dialog";

describe("Dialog in embedded scope", () => {
  it("DialogContent renders inside #chat-assistant-root when that element exists", () => {
    // Set up the embedded host element
    const host = document.createElement("div");
    host.id = "chat-assistant-root";
    document.body.appendChild(host);

    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Test</DialogTitle>
          <DialogDescription>Body text</DialogDescription>
        </DialogContent>
      </Dialog>,
      { container: host },
    );

    const title = screen.getByText("Test");
    // Walk up to confirm the dialog content is a descendant of #chat-assistant-root
    let node: HTMLElement | null = title;
    let foundHost = false;
    while (node) {
      if (node === host) { foundHost = true; break; }
      node = node.parentElement;
    }
    expect(foundHost).toBe(true);

    document.body.removeChild(host);
  });
});
