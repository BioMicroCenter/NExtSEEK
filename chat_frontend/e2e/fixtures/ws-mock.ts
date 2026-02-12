/**
 * Shared HTTP + WebSocket mock helper for Playwright E2E tests.
 *
 * Mocks the NExtSEEK Django REST API endpoints and per-query WebSocket
 * progress connections.
 */
import type { Page, WebSocketRoute } from "@playwright/test";

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
  };

  return controller;
}

/** Small delay helper */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
