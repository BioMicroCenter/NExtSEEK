import { describe, it, expect, beforeEach } from "vitest";
import { SessionAuthService } from "../sessionAuth";

describe("SessionAuthService", () => {
  beforeEach(() => {
    // Clear cookies
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("returns CSRF token header from cookie", () => {
    document.cookie = "csrftoken=abc123; path=/";
    const auth = new SessionAuthService();
    const headers = auth.getAuthHeaders();
    expect(headers).toEqual({ "X-CSRFToken": "abc123" });
  });

  it("returns empty headers when no CSRF cookie", () => {
    const auth = new SessionAuthService();
    const headers = auth.getAuthHeaders();
    expect(headers).toEqual({});
  });

  it("returns empty string for API base URL (relative)", () => {
    const auth = new SessionAuthService();
    expect(auth.getApiBaseUrl()).toBe("");
  });

  it("returns WebSocket base URL from window.location", () => {
    const auth = new SessionAuthService();
    const wsBase = auth.getWsBaseUrl();
    // jsdom uses http://localhost by default
    expect(wsBase).toMatch(/^ws/);
  });
});
