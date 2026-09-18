import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ReactElement } from "react";
import { act, render, screen, fireEvent } from "@testing-library/react";
import { EmbeddedApp } from "@/EmbeddedApp";
import { AppLayout } from "@/AppLayout";
import { NextseekApiService } from "@/lib/services/chatApi";
import type { ProgressEvent } from "@/lib/types/api";

// EmbeddedApp and AppLayout each hand-maintain their own progress handlers, and
// they have drifted at real cost before (#38). This renders each shell for real,
// sends a message, and plays the turn's callbacks into it by hand: the service's
// submitQuery is replaced, so nothing here touches a socket or the network.

interface TurnCallbacks {
  onProgress: (event: ProgressEvent) => void;
  onError: (error: string) => void;
  onNotice?: (message: string) => void;
}

let turn: TurnCallbacks | null = null;

beforeEach(() => {
  turn = null;
  // HeaderBar (AppLayout's toolbar) reads the colour-scheme preference on mount.
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes("/assistant/me/") ? { is_admin: false } : { total: 0, sessions: [] },
    })),
  );
  vi.spyOn(NextseekApiService.prototype, "submitQuery").mockImplementation(
    (_query, _mode, _opts, onProgress, onError, onNotice) => {
      turn = { onProgress, onError, onNotice };
      // Never settles: the turn stays in flight for as long as the test runs.
      return new Promise<void>(() => {});
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const SHELLS: Array<[string, () => ReactElement]> = [
  ["EmbeddedApp", () => <EmbeddedApp />],
  ["AppLayout", () => <AppLayout credentialError={null} />],
];

async function sendAQuestion(make: () => ReactElement): Promise<TurnCallbacks> {
  render(make());
  fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "How many samples?" } });
  fireEvent.click(screen.getByTestId("send-button"));
  await vi.waitFor(() => expect(turn).not.toBeNull());
  return turn!;
}

describe.each(SHELLS)("%s during a turn whose progress socket dropped", (_name, make) => {
  it("shows the service's notice and keeps the turn in flight", async () => {
    const t = await sendAQuestion(make);
    expect(t.onNotice, "the shell must hand submitQuery a notice callback").toBeTypeOf("function");

    act(() => t.onNotice!("Connection lost. Still waiting for the answer."));

    expect(await screen.findByText("Connection lost. Still waiting for the answer.")).toBeInTheDocument();
    expect(screen.queryByText(/^Error:/)).toBeNull();
    expect(screen.getByTestId("chat-input")).toBeDisabled();
  });
});
