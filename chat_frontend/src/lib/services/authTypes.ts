export interface AuthService {
  getAuthHeaders(): HeadersInit;
  getApiBaseUrl(): string;
  getWsBaseUrl(): string;
}
