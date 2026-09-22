/**
 * downloadSession: GET /nextseek_api/assistant/sessions/{sid}/download/, the
 * transcript plus every turn's files as one zip, saved under the server's name.
 *
 * The zip has no size bound (it collects every turn's raw API result), so the
 * tab must not hold it. The embedded shell authenticates by the session cookie,
 * which the browser adds to a plain same-origin link as well, so there the
 * browser is handed the link and streams the file to disk itself. The
 * standalone shell authenticates with a Basic header that no link can carry, so
 * it alone still fetches the body and saves it as a blob.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextseekApiService } from "../chatApi";
import { SessionAuthService } from "../sessionAuth";
import { BasicAuthService } from "../auth";

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
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["PK"], { type: "application/zip" }),
      headers: { get: () => 'attachment; filename="nessie-chat-sess1234.zip"' },
    } as unknown as Response);
  });

  afterEach(() => vi.restoreAllMocks());

  describe("in the embedded shell (session cookie)", () => {
    it("hands the browser a same-origin link instead of reading the zip into the tab", async () => {
      await new NextseekApiService(new SessionAuthService()).downloadSession("sess1234-aaaa");

      expect(global.fetch).not.toHaveBeenCalled();
      expect(URL.createObjectURL).not.toHaveBeenCalled();
      expect(anchor.getAttribute("href")).toBe(
        "/nextseek_api/assistant/sessions/sess1234-aaaa/download/",
      );
      // A download link, so the page never navigates away; the server's
      // Content-Disposition name takes precedence over this fallback.
      expect(anchor.download).toBe("nessie-chat-sess1234.zip");
      expect(anchor.click).toHaveBeenCalledTimes(1);
    });
  });

  describe("in the standalone shell (Basic header)", () => {
    const basic = () =>
      new NextseekApiService(new BasicAuthService("https://nextseek.example.org", "u", "p"));

    it("fetches with the header a link cannot carry and saves under the server's filename", async () => {
      await basic().downloadSession("sess1234-aaaa");

      expect(global.fetch).toHaveBeenCalledWith(
        "https://nextseek.example.org/nextseek_api/assistant/sessions/sess1234-aaaa/download/",
        expect.objectContaining({ headers: { Authorization: `Basic ${btoa("u:p")}` } }),
      );
      expect(anchor.getAttribute("href")).toBe("blob:x");
      expect(anchor.download).toBe("nessie-chat-sess1234.zip");
      expect(anchor.click).toHaveBeenCalled();
    });

    it("reports a refused download instead of saving the error body", async () => {
      vi.mocked(global.fetch).mockResolvedValue({ ok: false, status: 403 } as Response);

      await expect(basic().downloadSession("sess-1")).rejects.toThrow("403");
      expect(anchor.click).not.toHaveBeenCalled();
    });
  });
});
