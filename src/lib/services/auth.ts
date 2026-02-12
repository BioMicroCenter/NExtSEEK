import type { MeResponse } from "@/lib/types/api";

export class BasicAuthService {
  private user: string;
  private pass: string;
  private baseUrl: string;

  constructor(
    baseUrl = import.meta.env.VITE_API_BASE_URL ?? "",
    user = import.meta.env.VITE_API_USER ?? "",
    pass = import.meta.env.VITE_API_PASS ?? "",
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.user = user;
    this.pass = pass;
  }

  isConfigured(): boolean {
    return !!(this.baseUrl && this.user && this.pass);
  }

  getAuthHeader(): string {
    return "Basic " + btoa(`${this.user}:${this.pass}`);
  }

  getApiBaseUrl(): string {
    return this.baseUrl;
  }

  getWsBaseUrl(): string {
    return this.baseUrl.replace(/^http/, "ws");
  }

  async validateCredentials(): Promise<MeResponse> {
    const response = await fetch(
      `${this.baseUrl}/nextseek_api/assistant/me/`,
      {
        headers: { Authorization: this.getAuthHeader() },
      },
    );

    if (!response.ok) {
      throw new Error(
        response.status === 401
          ? "Invalid credentials"
          : `Credential check failed: ${response.status}`,
      );
    }

    return response.json();
  }
}

export const authService = new BasicAuthService();
