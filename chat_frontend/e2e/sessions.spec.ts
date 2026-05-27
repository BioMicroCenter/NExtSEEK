import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

const ALPHA = "11111111-1111-4111-8111-111111111111";
const BETA = "22222222-2222-4222-8222-222222222222";

test.describe("Saved chats sidebar", () => {
  test("renders empty state when no sessions exist", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([]);
    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });
    await expect(page.getByText(/start a new chat/i)).toBeVisible();
  });

  test("renders seeded sessions and switches on click", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([
      {
        session_id: ALPHA,
        title: "Alpha chat",
        created_at: "2026-05-12T00:00:00Z",
        updated_at: "2026-05-12T00:00:00Z",
        query_count: 1,
        preview: "alpha preview",
        turns: [{ bundle_id: 1, user_query: "alpha question", reply: "alpha reply", mode: "x" }],
      },
      {
        session_id: BETA,
        title: "Beta chat",
        created_at: "2026-05-12T00:00:00Z",
        updated_at: "2026-05-12T00:00:00Z",
        query_count: 1,
        preview: "beta preview",
        turns: [{ bundle_id: 1, user_query: "beta question", reply: "beta reply", mode: "x" }],
      },
    ]);

    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });

    await expect(page.getByRole("button", { name: "Alpha chat" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Beta chat" })).toBeVisible();

    // Click Beta → conversation rehydrates with beta reply.
    await page.getByRole("button", { name: "Beta chat" }).click();
    await expect(page.getByText("beta question")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("beta reply")).toBeVisible();
  });

  test("New chat sends force_new=true on first query", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([
      { session_id: ALPHA, title: "Alpha", created_at: "x", updated_at: "x", query_count: 0, preview: "" },
    ]);
    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });

    // Hit New chat button.
    await page.getByRole("button", { name: "New chat" }).click();

    // Submit a query.
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("brand new query");
    await input.press("Enter");
    await delay(300);

    // Body should contain force_new: true and NO session_id.
    const body = mock.receivedHttpBodies.find(
      (b: unknown) => (b as { query?: string })?.query === "brand new query",
    ) as { force_new?: boolean; session_id?: string } | undefined;
    expect(body).toBeTruthy();
    expect(body?.force_new).toBe(true);
    expect(body?.session_id).toBeUndefined();
  });

  test("clicking an existing session sends session_id, not force_new", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([
      {
        session_id: ALPHA,
        title: "Alpha chat",
        created_at: "x",
        updated_at: "x",
        query_count: 0,
        preview: "",
        turns: [],
      },
    ]);
    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });

    await page.getByRole("button", { name: "Alpha chat" }).click();
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("follow-up in alpha");
    await input.press("Enter");
    await delay(300);

    const body = mock.receivedHttpBodies.find(
      (b: unknown) => (b as { query?: string })?.query === "follow-up in alpha",
    ) as { force_new?: boolean; session_id?: string } | undefined;
    expect(body).toBeTruthy();
    expect(body?.session_id).toBe(ALPHA);
    expect(body?.force_new).toBeUndefined();
  });

  test("rename: inline edit → PATCH with new title", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([
      { session_id: ALPHA, title: "Alpha", created_at: "x", updated_at: "x", query_count: 0, preview: "" },
    ]);
    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });

    const row = page.locator('[aria-label="Saved chats"] >> .group').first();
    await row.hover();
    await row.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: /rename/i }).click();
    const input = page.getByRole("textbox", { name: "Rename session" });
    await input.fill("Alpha Renamed");
    await input.press("Enter");
    await delay(200);

    expect(mock.receivedPatches.find((p) => p.sessionId === ALPHA)).toMatchObject({
      sessionId: ALPHA,
      body: { title: "Alpha Renamed" },
    });
  });

  test("delete: confirm dialog → DELETE", async ({ page }) => {
    const mock = await setupMocks(page);
    mock.setSessions([
      { session_id: ALPHA, title: "Alpha", created_at: "x", updated_at: "x", query_count: 0, preview: "" },
    ]);
    await page.goto("/");
    await page.waitForSelector('[aria-label="Saved chats"]', { timeout: 10_000 });

    const row = page.locator('[aria-label="Saved chats"] >> .group').first();
    await row.hover();
    await row.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: /delete/i }).click();
    await page.getByRole("button", { name: /^delete$/i }).click();
    await delay(200);

    expect(mock.receivedDeletes).toContain(ALPHA);
  });
});
