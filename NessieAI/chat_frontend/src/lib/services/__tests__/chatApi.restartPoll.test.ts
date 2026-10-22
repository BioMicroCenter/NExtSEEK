import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";
import type { ProgressEvent } from "@/lib/types/api";

// A deploy or restart makes the progress poll answer 502/503/504 (or drop the connection) for a
// while. The turn is stored server-side, so the poll keeps asking, says so once, and any other
// failure still ends the turn. The socket never opens here: the poll is the path under test.

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
const ANSWER = { event: "query_complete", data: { reply: "There are 42.", bundle_id: 7, debug: {} } };
const RESTARTING = "NExtSEEK is restarting. Still waiting for the answer.";

function createMockAuth(): AuthService {
  return {
    getAuthHeaders: vi.fn().mockReturnValue({}),
    getApiBaseUrl: vi.fn().mockReturnValue("http://localhost"),
    getWsBaseUrl: vi.fn().mockReturnValue("ws://localhost"),
  };
}

type Step = { status: number } | { reject: Error } | { progress: ProgressEvent[] };

/** fetch: the POST answers with the task; each progress GET plays the next step (the last repeats). */
function stubFetch(steps: Step[]) {
  let n = 0;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return { ok: true, status: 200, json: async () => ({ task_id: TASK, session_id: "sess-1" }) };
    }
    const step = steps[Math.min(n++, steps.length - 1)];
    if ("reject" in step) throw step.reject;
    if ("status" in step) return { ok: false, status: step.status, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ status: "running", progress: step.progress }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function startTurn(service: NextseekApiService) {
  const delivered: ProgressEvent[] = [];
  const onError = vi.fn();
  const onNotice = vi.fn();
  const done = service.submitQuery("q", "standard", {}, (e: ProgressEvent) => delivered.push(e), onError, onNotice);
  await vi.waitFor(() => expect(ControlledSocket.instances).toHaveLength(1));
  ControlledSocket.instances[0].drop(1006); // never opened: the poll takes over
  await vi.advanceTimersByTimeAsync(0);
  return { done, delivered, onError, onNotice };
}

describe("the progress poll through a restart", () => {
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

  it.each([502, 503, 504])("keeps polling through a %i and delivers the answer", async (status) => {
    stubFetch([{ status }, { status }, { progress: [ANSWER] }]);
    const { done, delivered, onError, onNotice } = await startTurn(service);

    await vi.advanceTimersByTimeAsync(10000);
    await done;

    expect(onError).not.toHaveBeenCalled();
    expect(delivered.at(-1)).toEqual(ANSWER);
    expect(onNotice).toHaveBeenCalledTimes(1);
    expect(onNotice).toHaveBeenCalledWith(RESTARTING);
  });

  it("treats a dropped connection as a restart too", async () => {
    stubFetch([{ reject: new TypeError("Failed to fetch") }, { progress: [ANSWER] }]);
    const { done, delivered, onError, onNotice } = await startTurn(service);

    await vi.advanceTimersByTimeAsync(10000);
    await done;

    expect(onError).not.toHaveBeenCalled();
    expect(onNotice).toHaveBeenCalledWith(RESTARTING);
    expect(delivered.at(-1)).toEqual(ANSWER);
  });

  it("gives up after the silence limit while the server stays down", async () => {
    stubFetch([{ status: 503 }]);
    const { done, onError } = await startTurn(service);

    await vi.advanceTimersByTimeAsync(31 * 60 * 1000);
    await done;

    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("any other failing status still ends the turn at once", async () => {
    stubFetch([{ status: 500 }]);
    const { done, onError, onNotice } = await startTurn(service);

    await vi.advanceTimersByTimeAsync(0);
    await done;

    expect(onError).toHaveBeenCalledWith("Polling failed: 500");
    expect(onNotice).not.toHaveBeenCalled();
  });
});
