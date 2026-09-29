import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";
import type { ProgressEvent } from "@/lib/types/api";

// 13e.3: once the progress socket has opened, a drop used to end the turn with
// "Connection closed unexpectedly", although the turn goes on server-side and its
// answer lands in the task all the same. The poll fallback covered only a socket
// that never opened. These tests drive the socket by hand: it opens, delivers,
// errors and closes only when a test says so. Like a browser, a client-side
// close() does not fire onclose synchronously; a test fires it with drop().

class ControlledSocket {
  static instances: ControlledSocket[] = [];

  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closedWith: number | null = null;

  constructor(url: string) {
    this.url = url;
    ControlledSocket.instances.push(this);
  }

  close(code = 1000) {
    this.closedWith = code;
  }

  open() {
    this.onopen?.();
  }

  receive(event: string, data: Record<string, unknown> = {}) {
    this.onmessage?.({ data: JSON.stringify({ event, data }) });
  }

  fail() {
    this.onerror?.();
  }

  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

const TASK = "0f0e0d0c-0b0a-4908-8706-050403020100";
const PROGRESS_URL = `http://localhost/nextseek_api/assistant/tasks/${TASK}/progress/`;

const STARTED = { event: "agent_started", data: { agent: "entity", mode: "new_search" } };
const DONE_ENTITY = { event: "agent_complete", data: { agent: "entity", summary: "ok" } };
const ANSWER = { event: "query_complete", data: { reply: "There are 42.", bundle_id: 7, debug: {} } };

function createMockAuth(): AuthService {
  return {
    getAuthHeaders: vi.fn().mockReturnValue({}),
    getApiBaseUrl: vi.fn().mockReturnValue("http://localhost"),
    getWsBaseUrl: vi.fn().mockReturnValue("ws://localhost"),
  };
}

/** fetch: the POST answers with the task, each progress GET with the next snapshot. */
function stubFetch(snapshots: ProgressEvent[][]) {
  const progressGets: string[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return { ok: true, json: async () => ({ task_id: TASK, session_id: "sess-1" }) };
    }
    progressGets.push(url);
    const progress = snapshots[Math.min(progressGets.length, snapshots.length) - 1] ?? [];
    return { ok: true, json: async () => ({ status: "running", progress }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return progressGets;
}

async function startTurn(service: NextseekApiService) {
  const delivered: ProgressEvent[] = [];
  const onProgress = vi.fn((e: ProgressEvent) => delivered.push(e));
  const onError = vi.fn();
  const onNotice = vi.fn();
  const done = service.submitQuery("q", "standard", {}, onProgress, onError, onNotice);
  await vi.waitFor(() => expect(ControlledSocket.instances).toHaveLength(1));
  return { done, sock: ControlledSocket.instances[0], delivered, onError, onNotice };
}

const names = (events: ProgressEvent[]) => events.map((e) => e.event);

describe("a progress socket that drops after it opened", () => {
  let service: NextseekApiService;

  beforeEach(() => {
    vi.useFakeTimers();
    ControlledSocket.instances = [];
    vi.stubGlobal("WebSocket", ControlledSocket);
    service = new NextseekApiService(createMockAuth());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reads the answer by polling the task instead of giving up", async () => {
    const gets = stubFetch([[STARTED, DONE_ENTITY, ANSWER]]);
    const { done, sock, delivered, onError } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(onError).not.toHaveBeenCalled();
    expect(gets).toEqual([PROGRESS_URL]);
    expect(delivered.at(-1)).toEqual(ANSWER);
  });

  it("resumes after the events the socket already delivered, so none is shown twice", async () => {
    const gets = stubFetch([
      [STARTED, DONE_ENTITY],
      [STARTED, DONE_ENTITY, ANSWER],
    ]);
    const { done, sock, delivered } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.receive(DONE_ENTITY.event, DONE_ENTITY.data);
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    expect(names(delivered)).toEqual(["agent_started", "agent_complete"]);

    await vi.advanceTimersByTimeAsync(2000);
    await done;

    expect(gets).toHaveLength(2);
    expect(names(delivered)).toEqual(["agent_started", "agent_complete", "query_complete"]);
  });

  it("polls from the start when the socket dropped before delivering anything", async () => {
    stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered } = await startTurn(service);

    sock.open();
    sock.drop(1011);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });

  it("also polls after a clean close that came before the final event", async () => {
    stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered, onError } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.drop(1000);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(onError).not.toHaveBeenCalled();
    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });

  it("an error after open starts exactly one poll, however many close events follow", async () => {
    const gets = stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.fail();
    sock.drop(1006);
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(gets).toHaveLength(1);
    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });

  it("ignores anything the socket delivers once the poll has taken over", async () => {
    stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.fail();
    sock.receive(ANSWER.event, ANSWER.data);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });

  it("tells the user once that it is still waiting for the answer", async () => {
    stubFetch([[STARTED, ANSWER]]);
    const { done, sock, onNotice } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.fail();
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(onNotice).toHaveBeenCalledTimes(1);
    expect(onNotice.mock.calls[0][0]).toMatch(/still waiting for the answer/i);
  });

  it("reports a poll that fails after the drop as the turn's error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? { ok: true, json: async () => ({ task_id: TASK, session_id: "sess-1" }) }
          : { ok: false, status: 502 },
      ),
    );
    const { done, sock, onError } = await startTurn(service);

    sock.open();
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(onError).toHaveBeenCalledWith("Polling failed: 502");
  });
});

describe("a progress socket that finishes the turn", () => {
  let service: NextseekApiService;

  beforeEach(() => {
    vi.useFakeTimers();
    ControlledSocket.instances = [];
    vi.stubGlobal("WebSocket", ControlledSocket);
    service = new NextseekApiService(createMockAuth());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not poll after the final event and its normal close", async () => {
    const gets = stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered, onError, onNotice } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.receive(ANSWER.event, ANSWER.data);
    expect(sock.closedWith).toBe(1000);
    sock.drop(1000);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(gets).toEqual([]);
    expect(onError).not.toHaveBeenCalled();
    expect(onNotice).not.toHaveBeenCalled();
    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });

  it("delivers the answer once when the server's close races the final event", async () => {
    const gets = stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered } = await startTurn(service);

    sock.open();
    sock.receive(ANSWER.event, ANSWER.data);
    sock.receive("done", { status: "completed" });
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(2000);
    await done;

    expect(gets).toEqual([]);
    expect(delivered.filter((e) => e.event === "query_complete")).toHaveLength(1);
  });

  it("still polls from the start, without a notice, when the socket never opens", async () => {
    const gets = stubFetch([[STARTED, ANSWER]]);
    const { done, sock, delivered, onNotice } = await startTurn(service);

    sock.fail();
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(gets).toEqual([PROGRESS_URL]);
    expect(onNotice).not.toHaveBeenCalled();
    expect(names(delivered)).toEqual(["agent_started", "query_complete"]);
  });
});

// A poll that took over a dropped stream used to wait forever when the turn's
// thread never wrote its final event while the server kept answering. It now
// gives up after 30 minutes without a new progress event: the server's own rule
// for a pending or running task that has stopped moving (STALE_TASK_SECONDS).
const MINUTE = 60_000;

/** fetch whose progress list grows with fake time: `at` gives each event's minute. */
function stubTimedFetch(at: Array<[number, ProgressEvent]>) {
  const start = Date.now();
  const progressGets: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return { ok: true, json: async () => ({ task_id: TASK, session_id: "sess-1" }) };
      }
      progressGets.push(url);
      const elapsed = Date.now() - start;
      const progress = at.filter(([min]) => elapsed >= min * MINUTE).map(([, e]) => e);
      return { ok: true, json: async () => ({ status: "running", progress }) };
    }),
  );
  return progressGets;
}

describe("the poll that takes over a turn", () => {
  let service: NextseekApiService;

  beforeEach(() => {
    vi.useFakeTimers();
    ControlledSocket.instances = [];
    vi.stubGlobal("WebSocket", ControlledSocket);
    service = new NextseekApiService(createMockAuth());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("gives up after 30 minutes without a new event, saying a reload may still show the answer", async () => {
    const gets = stubTimedFetch([[0, STARTED]]);
    const { done, sock, onError } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(29 * MINUTE);
    expect(onError).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1 * MINUTE);

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatch(/30 minutes/);
    expect(onError.mock.calls[0][0]).toMatch(/reload/i);
    await done;
    const polled = gets.length;
    await vi.advanceTimersByTimeAsync(10 * MINUTE);
    expect(gets).toHaveLength(polled);
  });

  it("never cuts off a turn that keeps moving, however long it runs", async () => {
    stubTimedFetch([
      [0, STARTED],
      [20, DONE_ENTITY],
      [45, { event: "agent_started", data: { agent: "chatter", mode: "new_search" } }],
      [70, ANSWER],
    ]);
    const { done, sock, delivered, onError } = await startTurn(service);

    sock.open();
    sock.receive(STARTED.event, STARTED.data);
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(71 * MINUTE);
    await done;

    expect(onError).not.toHaveBeenCalled();
    expect(delivered.at(-1)).toEqual(ANSWER);
  });

  it("gives up the same way when the socket never opened", async () => {
    stubTimedFetch([[0, STARTED]]);
    const { done, sock, delivered, onError } = await startTurn(service);

    sock.fail();
    sock.drop(1006);
    await vi.advanceTimersByTimeAsync(30 * MINUTE);

    expect(names(delivered)).toEqual(["agent_started"]);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatch(/reload/i);
    await done;
  });
});
