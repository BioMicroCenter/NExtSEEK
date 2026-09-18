/**
 * downloadSession: GET /nextseek_api/assistant/sessions/{sid}/download/, the
 * transcript plus every turn's files as one zip, saved under the server's name.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextseekApiService } from "../chatApi";

function makeService() {
  const auth = {
    getApiBaseUrl: () => "https://nextseek.example.org",
    getAuthHeaders: () => ({ "X-CSRFToken": "tok" }),
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return new NextseekApiService(auth as any);
}

describe("downloadSession", () => {
  let anchor: HTMLAnchorElement;

  beforeEach(() => {
    anchor = document.createElement("a");
    anchor.click = vi.fn();
    vi.spyOn(document, "createElement").mockReturnValue(anchor);
    vi.spyOn(document.body, "appendChild").mockImplementation((n) => n);
    vi.spyOn(document.body, "removeChild").mockImplementation((n) => n);
    global.URL.createObjectURL = vi.fn(() => "blob:x");
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => vi.restoreAllMocks());

  it("asks for the session zip and saves it under the server's filename", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["PK"], { type: "application/zip" }),
      headers: { get: () => 'attachment; filename="nessie-chat-sess1234.zip"' },
    });

    await makeService().downloadSession("sess1234-aaaa");

    expect(global.fetch).toHaveBeenCalledWith(
      "https://nextseek.example.org/nextseek_api/assistant/sessions/sess1234-aaaa/download/",
      expect.objectContaining({ headers: { "X-CSRFToken": "tok" } }),
    );
    expect(anchor.download).toBe("nessie-chat-sess1234.zip");
    expect(anchor.click).toHaveBeenCalled();
  });

  it("reports a refused download instead of saving the error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 403 });

    await expect(makeService().downloadSession("sess-1")).rejects.toThrow("403");
    expect(anchor.click).not.toHaveBeenCalled();
  });
});
