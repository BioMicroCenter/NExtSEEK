import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe("Chat Flow", () => {
  test("sends query and shows user message", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    // App boots straight into chat (no login)
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("Find me mice treated with NDMA");
    await input.press("Enter");

    // User message should appear
    await expect(
      page.getByText("Find me mice treated with NDMA"),
    ).toBeVisible();

    // Verify HTTP POST received the query
    await delay(200);
    const queryBody = mock.receivedHttpBodies.find(
      (b: any) => b?.query === "Find me mice treated with NDMA",
    );
    expect(queryBody).toBeTruthy();
  });

  test("shows stepper during processing", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Send a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test query");
    await input.press("Enter");
    await delay(300);

    // Simulate agent pipeline
    mock.simulateAgentStarted("entity", "");
    await delay(50);

    // Should see stepper with entity step
    await expect(page.getByTitle("Extracting entities")).toBeVisible();
  });

  test("displays assistant response after query complete", async ({
    page,
  }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test query");
    await input.press("Enter");
    await delay(300);

    // Simulate full pipeline
    mock.simulateAgentStarted("entity", "");
    await delay(50);
    mock.simulateAgentComplete("entity", "Found: TIS");
    mock.simulateAgentStarted("parser", "");
    await delay(50);
    mock.simulateAgentComplete("parser", "mode=new_search");
    mock.simulateAgentStarted("api", "new_search");
    await delay(50);
    mock.simulateAgentComplete("api", "POST /samples/advanced_search/");
    mock.simulateAgentStarted("http", "new_search");
    await delay(50);
    mock.simulateAgentComplete("http", "200 OK, 42 results");
    mock.simulateAgentStarted("chatter", "new_search");
    await delay(50);
    mock.simulateAgentComplete("chatter", "Found 42 samples");
    mock.simulateQueryComplete("Found 42 TIS samples matching your query.", 1);

    // Assistant response should appear
    await expect(
      page.getByText("Found 42 TIS samples matching your query."),
    ).toBeVisible();
  });

  test("handles query error gracefully", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("bad query");
    await input.press("Enter");
    await delay(300);

    mock.simulateAgentStarted("entity", "");
    await delay(50);
    mock.simulateQueryError("Pipeline failed: connection timeout");

    // Error message should appear
    await expect(
      page.getByText("Error: Pipeline failed: connection timeout"),
    ).toBeVisible();
  });
});
