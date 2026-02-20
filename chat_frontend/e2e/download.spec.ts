import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe("Download", () => {
  test("download buttons are disabled before query, enabled after", async ({
    page,
  }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Open right sidebar
    await page.getByLabel("Toggle debug panel").click();

    // Download buttons should be disabled initially
    const jsonBtn = page.getByRole("button", { name: "JSON" });
    const metaBtn = page.getByRole("button", { name: "Metadata" });
    await expect(jsonBtn).toBeDisabled();
    await expect(metaBtn).toBeDisabled();

    // Close sidebar by pressing Escape, then run a query
    await page.keyboard.press("Escape");
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test");
    await input.press("Enter");
    await delay(300);

    mock.simulateAgentStarted("entity", "");
    mock.simulateAgentComplete("entity", "test");
    mock.simulateQueryComplete("Result.", 1);
    await delay(200);

    // Re-open sidebar
    await page.getByLabel("Toggle debug panel").click();

    // Buttons should now be enabled
    await expect(jsonBtn).toBeEnabled();
    await expect(metaBtn).toBeEnabled();
  });

  test("clicking download triggers fetch to bundle endpoint", async ({ page }) => {
    const mock = await setupMocks(page);

    // Track fetch calls to bundle endpoint
    let bundleFetched = false;
    await page.route("**/nextseek_api/assistant/sessions/*/bundles/**", (route) => {
      bundleFetched = true;
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: {
          "Content-Disposition": 'attachment; filename="result_1.json"',
        },
        body: JSON.stringify({ sample: "data", total: 42 }),
      });
    });

    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Complete a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("test");
    await input.press("Enter");
    await delay(300);

    mock.simulateAgentStarted("entity", "");
    mock.simulateAgentComplete("entity", "test");
    mock.simulateQueryComplete("Done.", 1);
    await delay(200);

    // Open right sidebar
    await page.getByLabel("Toggle debug panel").click();

    // Click JSON download button
    await page.getByRole("button", { name: "JSON" }).click();
    await delay(500);

    // Verify the bundle endpoint was fetched
    expect(bundleFetched).toBe(true);
  });
});
