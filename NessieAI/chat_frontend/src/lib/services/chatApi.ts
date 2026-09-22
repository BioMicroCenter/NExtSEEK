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

/**
 * How long a poll waits for a new progress event before it stops. Every event a
 * turn emits touches its task, and this is the server's own rule for a pending or
 * running task that has stopped moving: an orphan, not a turn in flight
 * (STALE_TASK_SECONDS, nextseek_api/assistant/session_debug.py). It measures
 * silence, not age, because an NS turn has no overall wall clock, and it sits above
 * the longest single silent step the engine allows (a 600 s report-writer call
 * with its one timeout retry).
 */
const POLL_SILENCE_LIMIT_MS = 30 * 60 * 1000;
const POLL_GAVE_UP =
  "No progress on this answer for 30 minutes, so the chat stopped waiting. " +
  "It may still appear if you reload the page.";

/** What the user is told while a dropped progress socket's turn is read by polling. */
const STREAM_LOST_NOTICE = "Connection lost. Still waiting for the answer.";

/** How a progress socket that opened came to its end. */
interface StreamOutcome {
  /** The turn's final event came through the socket. */
  finished: boolean;
  /** Progress events the socket delivered; a poll that takes over resumes after them. */
  delivered: number;
}

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
    opts: { sessionId?: string | null; forceNew?: boolean; forceRoute?: "auto" | "ns" | "cc"; useProd?: boolean; maxTurnLengthS?: number | null },
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
    onNotice?: (message: string) => void,
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
    // Admin-only per-turn timeout override (server admin-gates + clamps to the
    // env-bounded hard ceiling). Omitted when unset -> server uses its default.
    if (opts.maxTurnLengthS && opts.maxTurnLengthS > 0) {
      body.max_turn_length_s = opts.maxTurnLengthS;
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
    let stream: StreamOutcome;
    try {
      stream = await this.streamProgress(
        `${wsBase}/ws/assistant/progress/${taskId}/`,
        onProgress,
      );
    } catch {
      // WS failed, fall back to polling
      await this.pollProgress(baseUrl, taskId, onProgress, onError);
      return;
    }
    if (!stream.finished) {
      // The socket opened, then dropped before the turn's final event. The turn
      // goes on server-side and its answer lands in the task either way, so read
      // the rest by polling, starting after the events the socket delivered so
      // that none of them, the answer included, reaches the user twice.
      onNotice?.(STREAM_LOST_NOTICE);
      await this.pollProgress(baseUrl, taskId, onProgress, onError, stream.delivered);
    }
  }

  /**
   * Stream a task's progress over the WebSocket. Rejects only when the socket
   * never opens. Once it has opened it resolves exactly once: at the turn's
   * final event, or at the first error or close before it (whatever the close
   * code). From then on the socket delivers nothing more, so the poll that takes
   * over a dropped stream cannot repeat an event.
   */
  private streamProgress(
    url: string,
    onProgress: (event: ProgressEvent) => void,
  ): Promise<StreamOutcome> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      let opened = false;
      let settled = false;
      let delivered = 0;

      const settle = (finished: boolean) => {
        if (settled) return;
        settled = true;
        resolve({ finished, delivered });
      };

      ws.onopen = () => {
        opened = true;
      };

      ws.onmessage = (event: MessageEvent) => {
        if (settled) return;
        try {
          const parsed: ProgressEvent = JSON.parse(event.data as string);
          // The server's closing "done" frame is not one of the task's events.
          if (parsed.event !== "done") delivered += 1;
          const final =
            parsed.event === "query_complete" || parsed.event === "query_error";
          if (final) settle(true);
          onProgress(parsed);
          if (final) ws.close(1000);
        } catch {
          // ignore non-JSON
        }
      };

      ws.onerror = () => {
        if (!opened) {
          reject(new Error("WebSocket connection failed"));
        } else if (!settled) {
          // A browser follows this with a close; hand over now, and close so
          // that nothing more can arrive.
          settle(false);
          ws.close();
        }
      };

      ws.onclose = () => {
        if (!opened) {
          reject(new Error("WebSocket connection failed"));
        } else {
          settle(false);
        }
      };
    });
  }

  private async pollProgress(
    baseUrl: string,
    taskId: string,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
    startIndex = 0,
  ): Promise<void> {
    // Events before startIndex already reached the caller over the socket.
    let lastIndex = startIndex;
    let lastNewEventAt = Date.now();

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
        if (events.length > lastIndex) lastNewEventAt = Date.now();

        for (let i = lastIndex; i < events.length; i++) {
          onProgress(events[i]);

          if (
            events[i].event === "query_complete" ||
            events[i].event === "query_error"
          ) {
            return;
          }
        }

        lastIndex = Math.max(lastIndex, events.length);

        // The turn's thread died, or its final write failed, while the server
        // kept answering: without this the turn stays in flight for good.
        if (Date.now() - lastNewEventAt >= POLL_SILENCE_LIMIT_MS) {
          onError(POLL_GAVE_UP);
          return;
        }
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
    // Selection is `part`, NOT `format`. DRF owns `format` for content
    // negotiation and has no renderer named "metadata", so `?format=metadata`
    // 404'd before the view ran: that is why the Metadata button never worked.
    const isMetadata = format === "metadata";
    const query = isMetadata ? "?part=metadata" : "";
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/bundles/${bundleId}/${query}`,
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
    // Distinct names: both downloads used to be `result_<id>.json`, so saving
    // one after the other looked like the second button had done nothing.
    a.download = `bundle_${bundleId}${isMetadata ? ".metadata" : ""}.json`;
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

  /**
   * The whole chat as one zip: the transcript plus every turn's files, from
   * both the NS and the Container-CC artifact roots. Keyed on the session only,
   * so a turn older than the Debug panel's bundle is included too.
   *
   * The zip has no size bound (it carries every turn's raw API result). When the
   * browser holds the credential itself (the embedded shell's session cookie) it
   * is handed a plain link, so it streams the file to disk with its own progress
   * and this tab never holds the body; a refused download then shows as a failed
   * download in the browser. Basic auth needs a header no link can carry, so the
   * standalone shell still fetches the body and saves it as a blob.
   */
  async downloadSession(sessionId: string): Promise<void> {
    const endpoint =
      `${this.auth.getApiBaseUrl()}/nextseek_api/assistant/sessions/${sessionId}/download/`;
    const fallbackName = `nessie-chat-${sessionId.slice(0, 8)}.zip`;

    if (this.auth.browserCarriesCredentials) {
      // download keeps the page where it is; the server's Content-Disposition
      // name takes precedence over this fallback.
      saveVia(endpoint, fallbackName);
      return;
    }

    const response = await fetch(endpoint, { headers: { ...this.auth.getAuthHeaders() } });

    if (!response.ok) {
      throw new Error(`Failed to download the chat: ${response.status}`);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition");
    const filenameMatch = disposition?.match(/filename="?(.+?)"?$/);
    const filename = filenameMatch?.[1] ?? fallbackName;

    const url = URL.createObjectURL(blob);
    saveVia(url, filename);
    URL.revokeObjectURL(url);
  }
}

/** Click a temporary download link to `href`. */
function saveVia(href: string, filename: string): void {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
