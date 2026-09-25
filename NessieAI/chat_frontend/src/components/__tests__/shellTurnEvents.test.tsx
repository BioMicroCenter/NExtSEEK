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
  /** Resolve submitQuery's promise: the turn is over, as after its final event. */
  settle: () => void;
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
    (_query, _mode, _opts, onProgress, onError, onNotice) =>
      // Settles only when a test calls settle(): until then the turn stays in flight.
      new Promise<void>((resolve) => {
        turn = { onProgress, onError, onNotice, settle: resolve };
      }),
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
    error:
      "This took longer than the 3-minute limit, so I stopped. Say continue and I will carry on from where I got to.",
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
      await screen.findByText(
        "Error: This took longer than the 3-minute limit, so I stopped. Say continue and I will carry on from where I got to.",
      ),
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

    expect(await screen.findByText(/^Error: This took longer than the 3-minute limit/)).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-download")).toBeNull();
  });
});

// The graph-result reviewer's chips (#128) ride on an NS turn's query_complete at
// debug.suggestions. A chip's query is a whole question: one click sends it as the
// next message through the shell's own send path, and the backend's router decides.
const CHIP = {
  id: "b7-r0",
  source: "reviewer",
  kind: "split",
  label: "Only Converter",
  query: "Show samples for human subjects classified as Converter.",
  reason: "57 of 98 were Non-converter.",
};

const NS_WITH_CHIP: ProgressEvent = {
  event: "query_complete",
  data: {
    reply: "Found 98 samples for human subjects.",
    debug: { graph_review: { verdict: "suggest" }, suggestions: [CHIP] },
    bundle_id: 7,
    mode: "ns",
    session_id: "sess-1",
  },
};

// A Container-CC reply carries no debug at all.
const CC_REPLY: ProgressEvent = {
  event: "query_complete",
  data: { reply: "Wrote the report.", bundle_id: 0, mode: "cc", session_id: "sess-1" },
};

describe.each(SHELLS)("%s after a reply that carries the reviewer's chips", (_name, make) => {
  it("shows each chip under the reply, then sends its exact query as the next message", async () => {
    const submit = vi.mocked(NextseekApiService.prototype.submitQuery);
    const t = await sendAQuestion(make);

    await act(async () => t.onProgress(NS_WITH_CHIP));
    const chip = await screen.findByTestId("suggestion-chip");
    expect(chip).toHaveTextContent("Only Converter");
    expect(chip).toHaveAttribute("title", "57 of 98 were Non-converter.");
    expect(chip).toHaveAttribute("data-suggestion-id", "b7-r0");
    expect(chip).toHaveAttribute("data-source", "reviewer");

    // Disabled until the turn is over, like the composer.
    expect(chip).toBeDisabled();
    await act(async () => t.settle());
    await vi.waitFor(() => expect(screen.getByTestId("suggestion-chip")).toBeEnabled());

    const typed = submit.mock.calls[0];
    turn = null;
    fireEvent.click(screen.getByTestId("suggestion-chip"));
    await vi.waitFor(() => expect(turn).not.toBeNull());

    expect(submit).toHaveBeenCalledTimes(2);
    const [query, mode, opts] = submit.mock.calls[1];
    expect(query).toBe(CHIP.query);
    expect(mode).toEqual({ pipeline: "standard" });
    // The typed message's send options, with no override of its own: the router decides.
    expect(opts).toMatchObject({ forceRoute: "auto", useProd: false, maxTurnLengthS: null });
    expect(opts.forceRoute).toBe(typed[2].forceRoute);
    expect(opts.useProd).toBe(typed[2].useProd);
    expect(opts.maxTurnLengthS).toBe(typed[2].maxTurnLengthS);

    const users = screen.getAllByTestId("message-bubble").filter((b) => b.dataset.role === "user");
    expect(users[users.length - 1]).toHaveTextContent(CHIP.query);
    // The chip stays under its reply, disabled, while the turn it started runs.
    expect(screen.getByTestId("suggestion-chip")).toBeDisabled();
  });

  it("drops the older reply's chips once a newer reply arrives without any", async () => {
    const t = await sendAQuestion(make);
    await act(async () => t.onProgress(NS_WITH_CHIP));
    expect(await screen.findByTestId("suggestion-chip")).toBeInTheDocument();
    await act(async () => t.settle());

    turn = null;
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "Write me a report." } });
    fireEvent.click(screen.getByTestId("send-button"));
    await vi.waitFor(() => expect(turn).not.toBeNull());

    // Async act flushes the microtask the shell patches the new reply in.
    await act(async () => turn!.onProgress(CC_REPLY));
    expect(screen.getByText("Wrote the report.")).toBeInTheDocument();
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();
  });
});

describe.each(SHELLS)("%s after a reply without chips", (_name, make) => {
  it("shows no chip for a Container-CC reply, which carries no debug", async () => {
    const t = await sendAQuestion(make);
    await act(async () => t.onProgress(CC_REPLY));
    expect(screen.getByText("Wrote the report.")).toBeInTheDocument();
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();
  });

  it("shows no chip for an NS reply whose debug has no suggestions", async () => {
    const t = await sendAQuestion(make);
    await act(async () =>
      t.onProgress({
        event: "query_complete",
        data: { reply: "There are 42.", debug: { graph_review: { verdict: "ok" } }, bundle_id: 3, mode: "ns", session_id: "sess-1" },
      }),
    );
    expect(screen.getByText("There are 42.")).toBeInTheDocument();
    expect(screen.queryByTestId("suggestion-chip")).toBeNull();
  });
});
