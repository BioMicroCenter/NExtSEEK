import { describe, it, expect, vi, beforeEach } from "vitest";
import { BasicAuthService } from "../auth";

describe("BasicAuthService", () => {
  let auth: BasicAuthService;

  beforeEach(() => {
    auth = new BasicAuthService("http://localhost", "testuser", "testpass");
    vi.restoreAllMocks();
  });

  it("isConfigured returns true when all fields set", () => {
    expect(auth.isConfigured()).toBe(true);
  });

  it("isConfigured returns false when fields missing", () => {
    const incomplete = new BasicAuthService("", "", "");
    expect(incomplete.isConfigured()).toBe(false);
  });

  it("getAuthHeader returns valid Basic header", () => {
    const header = auth.getAuthHeader();
    expect(header).toBe("Basic " + btoa("testuser:testpass"));
  });

  it("getApiBaseUrl strips trailing slashes", () => {
    const svc = new BasicAuthService("http://example.com//", "u", "p");
    expect(svc.getApiBaseUrl()).toBe("http://example.com");
  });

  it("getWsBaseUrl replaces http with ws", () => {
    expect(auth.getWsBaseUrl()).toBe("ws://localhost");
  });

  it("getWsBaseUrl replaces https with wss", () => {
    const svc = new BasicAuthService("https://example.com", "u", "p");
    expect(svc.getWsBaseUrl()).toBe("wss://example.com");
  });

  it("validateCredentials succeeds with 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ username: "testuser", is_admin: true }),
      }),
    );

    const result = await auth.validateCredentials();
    expect(result.username).toBe("testuser");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost/nextseek_api/assistant/me/",
      expect.objectContaining({
        headers: { Authorization: auth.getAuthHeader() },
      }),
    );
  });

  it("validateCredentials throws on 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401 }),
    );

    await expect(auth.validateCredentials()).rejects.toThrow(
      "Invalid credentials",
    );
  });

  it("validateCredentials throws on other errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500 }),
    );

    await expect(auth.validateCredentials()).rejects.toThrow(
      "Credential check failed: 500",
    );
  });
});
