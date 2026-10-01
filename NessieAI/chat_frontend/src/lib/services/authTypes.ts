export interface AuthService {
  getAuthHeaders(): HeadersInit;
  getApiBaseUrl(): string;
  getWsBaseUrl(): string;
  /**
   * True when the browser itself sends the credential with every same-origin
   * request (the embedded shell's session cookie), so a plain link to an API
   * route is authenticated. Absent for Basic auth: no link can carry its header.
   */
  readonly browserCarriesCredentials?: boolean;
}
