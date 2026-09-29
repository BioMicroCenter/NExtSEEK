import type { AuthService } from "./authTypes";

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export class SessionAuthService implements AuthService {
  // Django's session cookie authenticates every same-origin request, a plain
  // link included; the CSRF header below is needed only for unsafe methods.
  readonly browserCarriesCredentials = true;

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
