import { useRef, useState, useCallback } from "react";
import { NextseekApiService } from "@/lib/services/chatApi";
import { authService } from "@/lib/services/auth";
import type { ProgressEvent, TestCase } from "@/lib/types/api";

interface SubmitQueryOpts {
  sessionId?: string | null;
  forceNew?: boolean;
  forceRoute?: "auto" | "ns" | "cc";
}

type SubmitMode = string | { pipeline: "standard" | "plan"; useProd?: boolean };

interface UseChatApiReturn {
  isQuerying: boolean;
  sessionId: string | null;
  apiService: NextseekApiService;
  getAuthoritativeSessionId: () => string | null;
  submitQuery: (
    query: string,
    mode: SubmitMode,
    opts: SubmitQueryOpts,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ) => void;
  fetchTestCases: () => Promise<TestCase[]>;
  downloadBundle: (sessionId: string, bundleId: number, format: string) => Promise<void>;
}

export function useChatApi(): UseChatApiReturn {
  const serviceRef = useRef(new NextseekApiService(authService));
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const submitQuery = useCallback(
    (
      query: string,
      mode: SubmitMode,
      opts: SubmitQueryOpts,
      onProgress: (event: ProgressEvent) => void,
      onError: (error: string) => void,
    ) => {
      setIsQuerying(true);

      serviceRef.current
        .submitQuery(query, mode, opts, onProgress, onError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [],
  );

  const fetchTestCases = useCallback(async () => {
    return serviceRef.current.fetchTestCases();
  }, []);

  const downloadBundle = useCallback(
    async (sid: string, bundleId: number, format: string) => {
      return serviceRef.current.downloadBundle(sid, bundleId, format);
    },
    [],
  );

  const getAuthoritativeSessionId = useCallback(
    () => serviceRef.current.sessionId,
    [],
  );

  return {
    isQuerying,
    sessionId,
    apiService: serviceRef.current,
    getAuthoritativeSessionId,
    submitQuery,
    fetchTestCases,
    downloadBundle,
  };
}
