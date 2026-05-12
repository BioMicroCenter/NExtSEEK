/**
 * Shared HTTP + WebSocket mock helper for Playwright E2E tests.
 *
 * Mocks the NExtSEEK Django REST API endpoints and per-query WebSocket
 * progress connections.
 */
import type { Page, WebSocketRoute } from "@playwright/test";

export interface SessionListSeed {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  query_count: number;
  preview: string;
  /** Optional: turns returned for ?include=turns */
  turns?: { bundle_id: number; user_query: string; reply: string; mode: string; ts?: string }[];
}

interface SessionState {
  sessions: SessionListSeed[];
}

export interface MockController {
  /** Send a raw progress event to the page */
  sendRaw: (data: unknown) => void;

  // --- Progress event simulators ---
  simulateAgentStarted: (agent: string, mode: string) => void;
  simulateAgentComplete: (agent: string, summary: string) => void;
  simulateQueryComplete: (reply: string, bundleId: number) => void;
  simulateQueryError: (error: string) => void;

  /** HTTP request bodies received from the page */
  receivedHttpBodies: unknown[];

  /** Replace the seeded session list returned by GET /sessions/. */
  setSessions: (sessions: SessionListSeed[]) => void;
  /** Records of PATCH/DELETE calls on /sessions/<id>/. */
  receivedPatches: { sessionId: string; body: unknown }[];
  receivedDeletes: string[];
}

const DEFAULT_SESSION_ID = "e2e-sess-00000001";
const DEFAULT_TASK_ID = "e2e-task-00000001";

const SAMPLE_TEST_CASES = [
  { id: "mice_ndma", prompt: "Find me mice treated with NDMA." },
  {
    id: "cd8_antibodies",
    prompt: "Find me all CD8 antibodies in the database.",
  },
];

/**
 * Set up full HTTP + WS mocking for E2E tests.
 *
 * Call BEFORE `page.goto("/")`.
 */
export async function setupMocks(page: Page): Promise<MockController> {
  const receivedHttpBodies: unknown[] = [];
  let wsRoute: WebSocketRoute | null = null;

  const sessionState: SessionState = { sessions: [] };
  const receivedPatches: { sessionId: string; body: unknown }[] = [];
  const receivedDeletes: string[] = [];

  // Mock GET /nextseek_api/assistant/me/ → validate credentials
  await page.route("**/nextseek_api/assistant/me/", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        username: "e2e-testuser",
        is_admin: true,
      }),
    });
  });

  // Mock POST /nextseek_api/assistant/query/async/ → return task_id
  await page.route("**/nextseek_api/assistant/query/async/", async (route) => {
    const request = route.request();
    try {
      const body = request.postDataJSON();
      receivedHttpBodies.push(body);
    } catch {
      // ignore
    }

    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: DEFAULT_TASK_ID,
        session_id: DEFAULT_SESSION_ID,
      }),
    });
  });

  // Mock GET /nextseek_api/assistant/test-cases/
  await page.route("**/nextseek_api/assistant/test-cases/", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ test_cases: SAMPLE_TEST_CASES }),
    });
  });

  // GET /sessions/<id>/ (with optional ?include=turns), PATCH, DELETE.
  // Registered BEFORE the bundles route so Playwright sees it last-added-first;
  // the regex anchors at the session-id trailing slash so /bundles/ paths don't match.
  await page.route(/\/nextseek_api\/assistant\/sessions\/[0-9a-z-]+\/(\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const match = url.pathname.match(/\/sessions\/([0-9a-z-]+)\/$/);
    const sessionId = match ? match[1] : "";
    const method = request.method();

    if (method === "GET") {
      const seed = sessionState.sessions.find((s) => s.session_id === sessionId);
      const include = (url.searchParams.get("include") || "").split(",").map((p) => p.trim());
      const payload: Record<string, unknown> = {
        session_id: sessionId,
        created_at: seed?.created_at ?? new Date().toISOString(),
        query_count: seed?.query_count ?? 0,
        has_results: (seed?.query_count ?? 0) > 0,
      };
      if (include.includes("turns")) {
        payload.title = seed?.title ?? "New chat";
        payload.turns = seed?.turns ?? [];
      }
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
      return;
    }

    if (method === "PATCH") {
      let body: unknown = {};
      try { body = request.postDataJSON(); } catch { /* ignore */ }
      receivedPatches.push({ sessionId, body });
      const seed = sessionState.sessions.find((s) => s.session_id === sessionId);
      const newTitle = (body as { title?: string })?.title ?? seed?.title ?? "New chat";
      if (seed) seed.title = newTitle;
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: sessionId,
          title: newTitle,
          created_at: seed?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
          query_count: seed?.query_count ?? 0,
          preview: seed?.preview ?? "",
        }),
      });
      return;
    }

    if (method === "DELETE") {
      receivedDeletes.push(sessionId);
      const idx = sessionState.sessions.findIndex((s) => s.session_id === sessionId);
      if (idx >= 0) sessionState.sessions.splice(idx, 1);
      route.fulfill({ status: 204, body: "" });
      return;
    }

    route.continue();
  });

  // GET /sessions/ → seeded list. Coexists with POST (create) on the same path.
  await page.route("**/nextseek_api/assistant/sessions/", (route) => {
    const method = route.request().method();
    if (method === "GET") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total: sessionState.sessions.length,
          sessions: sessionState.sessions,
        }),
      });
      return;
    }
    // POST (create) — not exercised by sessions.spec.ts but keep a 201 stub.
    route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "e2e-sess-created",
        created_at: new Date().toISOString(),
      }),
    });
  });

  // Mock GET /nextseek_api/assistant/sessions/*/bundles/* → fake JSON blob
  await page.route("**/nextseek_api/assistant/sessions/*/bundles/*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "Content-Disposition": 'attachment; filename="result_1.json"',
      },
      body: JSON.stringify({ sample: "data", total: 42 }),
    });
  });

  // Intercept WebSocket connections for progress
  await page.routeWebSocket(/\/ws\/assistant\/progress\//, (ws) => {
    wsRoute = ws;

    ws.onMessage(() => {
      // Client doesn't send messages on progress WS
    });
  });

  function sendRaw(data: unknown) {
    if (!wsRoute) throw new Error("WebSocket not connected yet");
    wsRoute.send(JSON.stringify(data));
  }

  const controller: MockController = {
    receivedHttpBodies,
    sendRaw,

    simulateAgentStarted(agent: string, mode: string) {
      sendRaw({
        event: "agent_started",
        data: { agent, mode },
      });
    },

    simulateAgentComplete(agent: string, summary: string) {
      sendRaw({
        event: "agent_complete",
        data: { agent, summary },
      });
    },

    simulateQueryComplete(reply: string, bundleId: number) {
      sendRaw({
        event: "query_complete",
        data: {
          reply,
          debug: { entity: "test", parser: "test" },
          bundle_id: bundleId,
        },
      });
    },

    simulateQueryError(error: string) {
      sendRaw({
        event: "query_error",
        data: { error },
      });
    },

    setSessions(seeds: SessionListSeed[]) {
      sessionState.sessions = seeds;
    },

    receivedPatches,
    receivedDeletes,
  };

  return controller;
}

/** Small delay helper */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
