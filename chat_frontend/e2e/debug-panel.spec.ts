import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe.skip("Debug Panel — removed in feature/chat-ui-styling", () => {
  test("shows empty state initially", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Open right sidebar
    await page.getByLabel("Toggle debug panel").click();

    await expect(
      page.getByText("Send a query to see debug output"),
    ).toBeVisible();
  });

  test("shows entries after query completes", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Send a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test query");
    await input.press("Enter");
    await delay(300);

    // Simulate pipeline
    mock.simulateAgentStarted("entity", "");
    mock.simulateAgentComplete("entity", "Found: TIS");
    mock.simulateAgentStarted("parser", "");
    mock.simulateAgentComplete("parser", "mode=new_search");
    mock.simulateQueryComplete("Found results.", 1);
    await delay(200);

    // Open right sidebar
    await page.getByLabel("Toggle debug panel").click();

    // Should see agent badges
    await expect(page.getByText("entity").first()).toBeVisible();
    await expect(page.getByText("parser").first()).toBeVisible();
  });

  test("accordion expands to show summary", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test");
    await input.press("Enter");
    await delay(300);

    mock.simulateAgentStarted("entity", "");
    mock.simulateAgentComplete("entity", "Entities: sampletype=TIS");
    mock.simulateQueryComplete("Done.", 1);
    await delay(200);

    // Open right sidebar
    await page.getByLabel("Toggle debug panel").click();

    // Click on the entity accordion trigger
    await page.getByText("entity").first().click();
    await delay(100);

    // Should see the summary text
    await expect(
      page.getByText("Entities: sampletype=TIS"),
    ).toBeVisible();
  });
});
