import type { AuthService } from "./authTypes";

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export class SessionAuthService implements AuthService {
  getAuthHeaders(): HeadersInit {
    const token = getCsrfToken();
    return token ? { "X-CSRFToken": token } : {};
  }

  getApiBaseUrl(): string {
    // Same origin — use relative URLs
    return "";
  }

  getWsBaseUrl(): string {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}`;
  }
}
