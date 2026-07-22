import type {
  AsyncQueryResponse,
  ProgressEvent,
  SessionListItem,
  SessionListResponse,
  SessionDetailWithTurns,
  TestCase,
  TestCasesResponse,
} from "@/lib/types/api";
import type { AuthService } from "./authTypes";

const POLL_INTERVAL = 2000;

async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    const first = body?.errors?.[0];
    if (first?.detail) return String(first.detail);
    if (first?.title) return String(first.title);
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // Body wasn't JSON or response.json() unsupported on the mock — fall through.
  }
  return null;
}

export class NextseekApiService {
  private auth: AuthService;
  private _sessionId: string | null = null;

  constructor(auth: AuthService) {
    this.auth = auth;
  }

  get sessionId(): string | null {
    return this._sessionId;
  }

  async submitQuery(
    query: string,
    mode: string | { pipeline: "standard" | "plan"; useProd?: boolean },
    opts: { sessionId?: string | null; forceNew?: boolean; forceRoute?: "auto" | "ns" | "cc"; useProd?: boolean },
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();

    // Build body — accept either the legacy plain-string mode or the {pipeline}
    // object from MessageInput. The PROD toggle is a sticky admin control now in
    // the Debug panel, read from opts at send time (mirrors force_route); the
    // server re-checks admin and ignores it for non-admins.
    const modeStr: string = typeof mode === "string" ? mode : mode.pipeline;
    const body: Record<string, unknown> = { query, mode: modeStr, use_prod: Boolean(opts.useProd) };
    if (opts.sessionId) {
      body.session_id = opts.sessionId;
    } else if (opts.forceNew) {
      body.force_new = true;
    }
    // Admin-only route override (server re-checks admin + ignores for non-admins).
    if (opts.forceRoute && opts.forceRoute !== "auto") {
      body.force_route = opts.forceRoute;
    }

    // 1. POST async query
    let taskId: string;
    try {
      const response = await fetch(
        // Routed through the additive cc-assistant endpoint: the dmac_assistant
        // BAML router decides per query between the NExtSEEK pipeline (NS) and
        // the sandboxed Container-Claude-Code path. It creates the SAME
        // QueryTask, so the WS (ws/assistant/progress/) + poll + sessions calls
        // below stay on the existing assistant routes and work unchanged.
        `${baseUrl}/nextseek_api/cc-assistant/query/async/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...this.auth.getAuthHeaders(),
          },
          body: JSON.stringify(body),
        },
      );

      if (!response.ok) {
        const detail = await readErrorDetail(response);
        throw new Error(
          detail
            ? `Query submission failed: ${response.status} — ${detail}`
            : `Query submission failed: ${response.status}`,
        );
      }

      const data: AsyncQueryResponse = await response.json();
      taskId = data.task_id;
      this._sessionId = data.session_id;
    } catch (err) {
      onError(err instanceof Error ? err.message : "Query submission failed");
      return;
    }

    // 2. Open WS for progress
    const wsBase = this.auth.getWsBaseUrl();
    try {
      await this.streamProgress(
        `${wsBase}/ws/assistant/progress/${taskId}/`,
        onProgress,
        onError,
      );
    } catch {
      // WS failed, fall back to polling
      await this.pollProgress(baseUrl, taskId, onProgress, onError);
    }
  }

  private streamProgress(
    url: string,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      let opened = false;

      ws.onopen = () => {
        opened = true;
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const parsed: ProgressEvent = JSON.parse(event.data as string);
          onProgress(parsed);

          if (
            parsed.event === "query_complete" ||
            parsed.event === "query_error"
          ) {
            ws.close(1000);
            resolve();
          }
        } catch {
          // ignore non-JSON
        }
      };

      ws.onerror = () => {
        if (!opened) {
          reject(new Error("WebSocket connection failed"));
        }
      };

      ws.onclose = (event: CloseEvent) => {
        if (!opened) {
          reject(new Error("WebSocket connection failed"));
        } else if (event.code !== 1000) {
          onError("Connection closed unexpectedly");
          resolve();
        } else {
          resolve();
        }
      };
    });
  }

  private async pollProgress(
    baseUrl: string,
    taskId: string,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ): Promise<void> {
    let lastIndex = 0;

    // eslint-disable-next-line no-constant-condition
    while (true) {
      try {
        const response = await fetch(
          `${baseUrl}/nextseek_api/assistant/tasks/${taskId}/progress/`,
          {
            headers: { ...this.auth.getAuthHeaders() },
          },
        );

        if (!response.ok) {
          onError(`Polling failed: ${response.status}`);
          return;
        }

        const data = await response.json();
        const events: ProgressEvent[] = data.progress ?? [];

        for (let i = lastIndex; i < events.length; i++) {
          onProgress(events[i]);

          if (
            events[i].event === "query_complete" ||
            events[i].event === "query_error"
          ) {
            return;
          }
        }

        lastIndex = events.length;
      } catch (err) {
        onError(
          err instanceof Error ? err.message : "Polling failed",
        );
        return;
      }

      await new Promise((r) => setTimeout(r, POLL_INTERVAL));
    }
  }

  async fetchTestCases(): Promise<TestCase[]> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/test-cases/`,
      {
        headers: { ...this.auth.getAuthHeaders() },
      },
    );

    if (!response.ok) {
      throw new Error(`Failed to fetch test cases: ${response.status}`);
    }

    const data: TestCasesResponse = await response.json();
    return data.test_cases;
  }

  async downloadBundle(
    sessionId: string,
    bundleId: number,
    format: string,
  ): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/bundles/${bundleId}/?format=${format}`,
      {
        headers: { ...this.auth.getAuthHeaders() },
      },
    );

    if (!response.ok) {
      throw new Error(`Failed to download: ${response.status}`);
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `result_${bundleId}.${format === "json" ? "json" : "json"}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async downloadArtifact(
    sessionId: string,
    bundleId: number,
    artifactKey: string,
  ): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/bundles/${bundleId}/artifacts/${artifactKey}/`,
      {
        headers: { ...this.auth.getAuthHeaders() },
      },
    );

    if (!response.ok) {
      throw new Error(`Failed to download artifact: ${response.status}`);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition");
    const filenameMatch = disposition?.match(/filename="?(.+?)"?$/);
    const filename = filenameMatch?.[1] ?? `${artifactKey}_${bundleId}.xlsx`;

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async fetchBundle(
    sessionId: string,
    bundleId: number,
  ): Promise<Record<string, unknown>> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/bundles/${bundleId}/`,
      {
        headers: { ...this.auth.getAuthHeaders() },
      },
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch bundle: ${response.status}`);
    }
    return response.json();
  }

  async listSessions(): Promise<SessionListResponse> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/`,
      { headers: { ...this.auth.getAuthHeaders() } },
    );
    if (!response.ok) {
      throw new Error(`Failed to list sessions: ${response.status}`);
    }
    return response.json();
  }

  async renameSession(sessionId: string, title: string): Promise<SessionListItem> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...this.auth.getAuthHeaders(),
        },
        body: JSON.stringify({ title }),
      },
    );
    if (!response.ok) {
      throw new Error(`Failed to rename session: ${response.status}`);
    }
    return response.json();
  }

  async deleteSession(sessionId: string): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/`,
      {
        method: "DELETE",
        headers: { ...this.auth.getAuthHeaders() },
      },
    );
    if (!response.ok) {
      throw new Error(`Failed to delete session: ${response.status}`);
    }
  }

  async fetchSessionTurns(sessionId: string): Promise<SessionDetailWithTurns> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/?include=turns`,
      { headers: { ...this.auth.getAuthHeaders() } },
    );
    if (!response.ok) {
      throw new Error(`Failed to load session: ${response.status}`);
    }
    return response.json();
  }

  downloadSearchAsExcel(
    data: Record<string, unknown>[],
    filename: string,
  ): void {
    import("xlsx").then((XLSX) => {
      const ws = XLSX.utils.json_to_sheet(data);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Search Results");
      XLSX.writeFile(wb, filename);
    });
  }

  async uploadFiles(files: File[]): Promise<{ job_id: string }> {
    const baseUrl = this.auth.getApiBaseUrl();
    const fd = new FormData();
    files.forEach((f) => fd.append("file", f));
    const r = await fetch(`${baseUrl}/nextseek_api/cc-assistant/upload/`, {
      method: "POST",
      body: fd,
      credentials: "include",
      headers: { ...this.auth.getAuthHeaders() },
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async pollUpload(jobId: string): Promise<{ state: string; result?: unknown }> {
    const baseUrl = this.auth.getApiBaseUrl();
    const r = await fetch(`${baseUrl}/nextseek_api/cc-assistant/upload/status/${jobId}/`, {
      credentials: "include",
      headers: { ...this.auth.getAuthHeaders() },
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async downloadCcArtifact(sessionId: string, key: string): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();
    const r = await fetch(
      `${baseUrl}/nextseek_api/cc-assistant/artifacts/${sessionId}/download/?key=${encodeURIComponent(key)}`,
      { credentials: "include", headers: { ...this.auth.getAuthHeaders() } },
    );
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const disposition = r.headers.get("Content-Disposition");
    const filenameMatch = disposition?.match(/filename="?(.+?)"?$/);
    const filename = filenameMatch?.[1] ?? key.split("/").pop() ?? "artifact";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
}
