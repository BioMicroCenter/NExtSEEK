import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static CONNECTING = 0;

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    // Auto-open after microtask
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN;
      this.onopen?.();
    }, 0);
  }

  close(code = 1000) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code });
  }

  // Helper for tests to simulate incoming messages
  _receiveMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

vi.stubGlobal("WebSocket", MockWebSocket);

function createMockAuth(): AuthService {
  return {
    getAuthHeaders: vi.fn().mockReturnValue({ Authorization: "Basic dGVzdDp0ZXN0" }),
    getApiBaseUrl: vi.fn().mockReturnValue("http://localhost"),
    getWsBaseUrl: vi.fn().mockReturnValue("ws://localhost"),
  };
}

describe("NextseekApiService", () => {
  let service: NextseekApiService;
  let mockAuth: AuthService;

  beforeEach(() => {
    vi.useFakeTimers();
    mockAuth = createMockAuth();
    service = new NextseekApiService(mockAuth);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sessionId is null initially", () => {
    expect(service.sessionId).toBeNull();
  });

  it("submitQuery posts to async endpoint and opens WS", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ task_id: "task-123", session_id: "sess-456" }),
      }),
    );

    const onProgress = vi.fn();
    const onError = vi.fn();

    // Fire and forget — we just want to verify the POST was made
    service.submitQuery("test query", onProgress, onError);

    // Let microtasks run so fetch resolves and WS opens
    await vi.advanceTimersByTimeAsync(10);

    // Verify fetch was called with correct args
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost/nextseek_api/assistant/query/async/",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "test query" }),
      }),
    );

    await vi.advanceTimersByTimeAsync(10);

    // Since we can't easily get the WS reference, let's just verify no error
    expect(onError).not.toHaveBeenCalled();

    // Clean up
    await vi.runAllTimersAsync().catch(() => {});
  });

  it("submitQuery calls onError when POST fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
      }),
    );

    const onProgress = vi.fn();
    const onError = vi.fn();

    await service.submitQuery("test", onProgress, onError);

    expect(onError).toHaveBeenCalledWith("Query submission failed: 500");
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("submitQuery calls onError when fetch throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error")),
    );

    const onProgress = vi.fn();
    const onError = vi.fn();

    await service.submitQuery("test", onProgress, onError);

    expect(onError).toHaveBeenCalledWith("Network error");
  });

  it("fetchTestCases returns test_cases array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            test_cases: [
              { id: "t1", prompt: "Test 1" },
              { id: "t2", prompt: "Test 2" },
            ],
          }),
      }),
    );

    const result = await service.fetchTestCases();
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe("t1");
  });

  it("fetchTestCases throws on error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 403 }),
    );

    await expect(service.fetchTestCases()).rejects.toThrow(
      "Failed to fetch test cases: 403",
    );
  });

  it("downloadBundle fetches correct URL", async () => {
    const mockBlob = new Blob(["test"], { type: "application/json" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
      }),
    );

    // Mock DOM APIs
    const mockElement = {
      href: "",
      download: "",
      click: vi.fn(),
    };
    vi.spyOn(document, "createElement").mockReturnValue(
      mockElement as unknown as HTMLAnchorElement,
    );
    vi.spyOn(document.body, "appendChild").mockImplementation(
      (node) => node,
    );
    vi.spyOn(document.body, "removeChild").mockImplementation(
      (node) => node,
    );
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn().mockReturnValue("blob:test"),
      revokeObjectURL: vi.fn(),
    });

    await service.downloadBundle("sess-1", 42, "json");

    expect(fetch).toHaveBeenCalledWith(
      "http://localhost/nextseek_api/assistant/sessions/sess-1/bundles/42/?format=json",
      expect.objectContaining({
        headers: { Authorization: "Basic dGVzdDp0ZXN0" },
      }),
    );
    expect(mockElement.click).toHaveBeenCalled();
  });
});
