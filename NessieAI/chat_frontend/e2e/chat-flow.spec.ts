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
    // ProcessingStepper renders each step's label as text and puts step.detail in
    // the title attribute, so the label is found by its exact text in the stepper.
    await expect(page.getByTestId("stepper").getByText("Extracting entities", { exact: true })).toBeVisible();
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

  test("a suggestion chip sends its query as the next message", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("Show samples for human subjects");
    await input.press("Enter");
    await delay(300);

    const query = "Show samples for human subjects classified as Converter.";
    mock.simulateQueryComplete("Found 98 samples for human subjects.", 7, [
      {
        id: "b7-r0",
        source: "reviewer",
        kind: "split",
        label: "Only Converter",
        query,
        reason: "57 of 98 were Non-converter.",
      },
    ]);

    const chip = page.getByTestId("suggestion-chip");
    await expect(chip).toHaveText("Only Converter");
    await expect(chip).toHaveAttribute("data-suggestion-id", "b7-r0");
    await expect(chip).toHaveAttribute("title", "57 of 98 were Non-converter.");
    await expect(chip).toBeEnabled();
    await chip.click();

    // The chip's query goes out as the next message, through the typed send path.
    await expect(
      page.locator('[data-testid="message-bubble"][data-role="user"]').last(),
    ).toHaveText(query);
    await expect.poll(() => mock.receivedHttpBodies.length).toBe(2);
    const typed = mock.receivedHttpBodies[0] as Record<string, unknown>;
    const sent = mock.receivedHttpBodies[1] as Record<string, unknown>;
    expect(sent.query).toBe(query);
    expect(sent.mode).toBe(typed.mode);
    expect(sent.use_prod).toBe(typed.use_prod);
    expect(sent).not.toHaveProperty("force_route");
    expect(sent).not.toHaveProperty("max_turn_length_s");
    // Disabled while the turn it started is in flight.
    await expect(chip).toBeDisabled();
  });
});
