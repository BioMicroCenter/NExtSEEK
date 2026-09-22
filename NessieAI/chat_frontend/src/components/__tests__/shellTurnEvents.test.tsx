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
  // A terminal event adopts its session and pushes /chat/<id>; start every test
  // at the root, or the next shell mounts into that chat and rehydrates over the test.
  window.history.replaceState(null, "", "/");
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

// A Container-CC turn stopped at its time limit still publishes what it wrote
// (NessieAI/cc/cc_engine.py, run_cc_turn), and its query_error carries those files
// in `artifacts`, in the same shape as a completed CC turn's.
const TIMED_OUT: ProgressEvent = {
  event: "query_error",
  data: {
    error: "Container-CC turn exceeded the 180s limit and was stopped.",
    reason: "exec_timeout",
    agent: "container_cc",
    cc_session_id: "cc-1",
    artifacts: [
      { artifact_type: "file", key: "run-1/report.csv", label: "report.csv", file_format: "csv" },
    ],
    cc_raw_files: ["/dmac/users/p/u/output/raw/rows.json"],
    session_id: "sess-9",
  },
};

describe.each(SHELLS)("%s after a Container-CC turn that timed out", (_name, make) => {
  it("shows the files it published beside the error, downloadable like a completed turn's", async () => {
    const download = vi
      .spyOn(NextseekApiService.prototype, "downloadCcArtifact")
      .mockResolvedValue(undefined);
    const t = await sendAQuestion(make);

    act(() => t.onProgress(TIMED_OUT));

    expect(
      await screen.findByText("Error: Container-CC turn exceeded the 180s limit and was stopped."),
    ).toBeInTheDocument();
    const link = await screen.findByTestId("artifact-download");
    expect(link).toHaveTextContent("report.csv");

    fireEvent.click(link);
    expect(download).toHaveBeenCalledWith("sess-9", "run-1/report.csv");
  });

  it("shows no download for an error that published nothing", async () => {
    const t = await sendAQuestion(make);

    act(() =>
      t.onProgress({
        event: "query_error",
        data: { ...TIMED_OUT.data, artifacts: null, cc_raw_files: [] },
      }),
    );

    expect(await screen.findByText(/^Error: Container-CC turn exceeded/)).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-download")).toBeNull();
  });
});
