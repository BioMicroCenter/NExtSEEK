import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

vi.mock("@/lib/services/auth", () => ({
  authService: {
    isConfigured: vi.fn(),
    validateCredentials: vi.fn(),
    getAuthHeader: vi.fn(),
    getApiBaseUrl: vi.fn(),
    getWsBaseUrl: vi.fn(),
  },
}));

import { useCredentialCheck } from "../useAuth";
import { authService } from "@/lib/services/auth";

const mockAuthService = authService as unknown as {
  isConfigured: ReturnType<typeof vi.fn>;
  validateCredentials: ReturnType<typeof vi.fn>;
};

describe("useCredentialCheck", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns error when credentials are not configured", async () => {
    mockAuthService.isConfigured.mockReturnValue(false);

    const { result } = renderHook(() => useCredentialCheck());

    await waitFor(() => {
      expect(result.current.isReady).toBe(true);
    });

    expect(result.current.isValid).toBe(false);
    expect(result.current.error).toContain("Missing credentials");
  });

  it("returns isValid when credentials are valid", async () => {
    mockAuthService.isConfigured.mockReturnValue(true);
    mockAuthService.validateCredentials.mockResolvedValue({
      username: "testuser",
      is_admin: true,
    });

    const { result } = renderHook(() => useCredentialCheck());

    await waitFor(() => {
      expect(result.current.isReady).toBe(true);
    });

    expect(result.current.isValid).toBe(true);
    expect(result.current.username).toBe("testuser");
    expect(result.current.error).toBeNull();
  });

  it("returns error when validation fails", async () => {
    mockAuthService.isConfigured.mockReturnValue(true);
    mockAuthService.validateCredentials.mockRejectedValue(
      new Error("Invalid credentials"),
    );

    const { result } = renderHook(() => useCredentialCheck());

    await waitFor(() => {
      expect(result.current.isReady).toBe(true);
    });

    expect(result.current.isValid).toBe(false);
    expect(result.current.error).toBe("Invalid credentials");
  });
});
