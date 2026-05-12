import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";

const baseUrl = "http://api.test";

class StubAuth implements AuthService {
  getAuthHeaders(): HeadersInit {
    return { "X-Test-Auth": "1" };
  }
  getApiBaseUrl(): string {
    return baseUrl;
  }
  getWsBaseUrl(): string {
    return "ws://api.test";
  }
}

function mockFetch(response: { ok?: boolean; status?: number; json?: () => Promise<unknown>; blob?: () => Promise<Blob> }) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: response.json ?? (() => Promise.resolve({})),
    blob: response.blob ?? (() => Promise.resolve(new Blob())),
    headers: new Headers(),
  });
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}

describe("NextseekApiService — sessions methods", () => {
  let svc: NextseekApiService;

  beforeEach(() => {
    svc = new NextseekApiService(new StubAuth());
  });

  it("listSessions GETs /sessions/ and returns the parsed body", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({
        total: 1,
        sessions: [{ session_id: "uuid-1", title: "t", created_at: "x", updated_at: "x", query_count: 0, preview: "" }],
      }),
    });
    const out = await svc.listSessions();
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/`,
      expect.objectContaining({ headers: expect.objectContaining({ "X-Test-Auth": "1" }) }),
    );
    expect(out.total).toBe(1);
    expect(out.sessions[0].session_id).toBe("uuid-1");
  });

  it("renameSession PATCHes title and returns the updated row", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({ session_id: "uuid-1", title: "renamed", created_at: "x", updated_at: "x", query_count: 0, preview: "" }),
    });
    const out = await svc.renameSession("uuid-1", "renamed");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/`,
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "Content-Type": "application/json", "X-Test-Auth": "1" }),
        body: JSON.stringify({ title: "renamed" }),
      }),
    );
    expect(out.title).toBe("renamed");
  });

  it("renameSession throws on non-2xx", async () => {
    mockFetch({ ok: false, status: 422 });
    await expect(svc.renameSession("uuid-1", "")).rejects.toThrow(/422/);
  });

  it("deleteSession DELETEs the row", async () => {
    const fetchSpy = mockFetch({ ok: true, status: 204 });
    await svc.deleteSession("uuid-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("fetchSessionTurns GETs with ?include=turns", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({
        session_id: "uuid-1",
        created_at: "x",
        query_count: 1,
        has_results: true,
        title: "t",
        turns: [{ bundle_id: 1, user_query: "hi", reply: "hello", mode: "x" }],
      }),
    });
    const out = await svc.fetchSessionTurns("uuid-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/?include=turns`,
      expect.anything(),
    );
    expect(out.turns).toHaveLength(1);
  });

  describe("submitQuery body shape", () => {
    // Helper: stub WebSocket to throw synchronously (WS fails → poll fallback),
    // and set up fetch so the POST returns the task-id and the poll call
    // immediately returns non-ok so pollProgress exits without looping.
    function mockFetchForSubmit(taskId: string, sessionId: string) {
      const fn = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ task_id: taskId, session_id: sessionId }),
          headers: new Headers(),
        })
        // Poll endpoint — non-ok causes pollProgress to call onError and return
        .mockResolvedValue({
          ok: false,
          status: 503,
          json: () => Promise.resolve({}),
          headers: new Headers(),
        });
      globalThis.fetch = fn as unknown as typeof fetch;
      vi.stubGlobal("WebSocket", class { constructor() { throw new Error("no ws"); } });
      return fn;
    }

    it("includes session_id when opts.sessionId is set", async () => {
      const fetchSpy = mockFetchForSubmit("t", "uuid-1");
      const onProgress = vi.fn();
      const onError = vi.fn();
      await svc.submitQuery("hi", "standard", { sessionId: "uuid-1" }, onProgress, onError);
      const firstCall = fetchSpy.mock.calls[0];
      expect(firstCall[0]).toBe(`${baseUrl}/nextseek_api/assistant/query/async/`);
      const body = JSON.parse((firstCall[1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard", session_id: "uuid-1" });
    });

    it("includes force_new when opts.forceNew is true", async () => {
      const fetchSpy = mockFetchForSubmit("t", "uuid-new");
      await svc.submitQuery("hi", "standard", { forceNew: true }, vi.fn(), vi.fn());
      const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard", force_new: true });
    });

    it("omits both when opts is empty", async () => {
      const fetchSpy = mockFetchForSubmit("t", "uuid-x");
      await svc.submitQuery("hi", "standard", {}, vi.fn(), vi.fn());
      const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard" });
    });
  });
});
