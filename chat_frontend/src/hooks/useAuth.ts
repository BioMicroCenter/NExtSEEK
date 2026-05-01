import { useState, useEffect } from "react";
import { authService } from "@/lib/services/auth";

interface UseCredentialCheckReturn {
  isReady: boolean;
  isValid: boolean;
  error: string | null;
  username: string | null;
}

export function useCredentialCheck(): UseCredentialCheckReturn {
  const [isReady, setIsReady] = useState(false);
  const [isValid, setIsValid] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    if (!authService.isConfigured()) {
      setError(
        "Missing credentials. Set VITE_API_BASE_URL, VITE_API_USER, and VITE_API_PASS in .env",
      );
      setIsReady(true);
      return;
    }

    authService
      .validateCredentials()
      .then((me) => {
        setIsValid(true);
        setUsername(me.username);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Credential validation failed");
      })
      .finally(() => {
        setIsReady(true);
      });
  }, []);

  return { isReady, isValid, error, username };
}
