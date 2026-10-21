import { describe, it, expect, vi, afterEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";

// A new question sent while NExtSEEK restarts must say so, and must never be sent twice: an
// nginx 502 can follow a crash mid-request, so a resend could start the turn two times.

const RESTARTING = "NExtSEEK is restarting. Send your question again in a minute.";

function createMockAuth(): AuthService {
  return {
    getAuthHeaders: vi.fn().mockReturnValue({}),
    getApiBaseUrl: vi.fn().mockReturnValue("http://localhost"),
    getWsBaseUrl: vi.fn().mockReturnValue("ws://localhost"),
  };
}

async function submit(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  const onError = vi.fn();
  await new NextseekApiService(createMockAuth()).submitQuery("q", "standard", {}, vi.fn(), onError, vi.fn());
  return onError;
}

describe("submitting a question through a restart", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([502, 503, 504])("says the app is restarting on a %i with no JSON", async (status) => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status,
      json: async () => {
        throw new SyntaxError("not json");
      },
    }));
    const onError = await submit(fetchMock);
    expect(onError).toHaveBeenCalledWith(RESTARTING);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("says it when the connection is refused", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const onError = await submit(fetchMock);
    expect(onError).toHaveBeenCalledWith(RESTARTING);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a 503 detail, such as the drain message", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: "Draining for a restart." }),
    }));
    const onError = await submit(fetchMock);
    expect(onError).toHaveBeenCalledWith("Query submission failed: 503 — Draining for a restart.");
  });

  it("leaves other failures alone", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({}) }));
    const onError = await submit(fetchMock);
    expect(onError).toHaveBeenCalledWith("Query submission failed: 400");
  });
});
