import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe("Test Runner", () => {
  test("displays test cases in left sidebar", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(500); // Wait for test cases to load

    // Open left sidebar
    await page.getByLabel("Toggle tests panel").click();

    // Should show the mock test cases
    await expect(page.getByText("mice_ndma")).toBeVisible();
    await expect(page.getByText("cd8_antibodies")).toBeVisible();
  });

  test("clicking run sends the test query", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(500);

    // Open left sidebar
    await page.getByLabel("Toggle tests panel").click();
    await delay(100);

    // Click run on first test
    await page.getByLabel("Run test mice_ndma").click();

    // The query should appear as a user message in the chat area
    await expect(
      page.locator(".rounded-2xl.rounded-br-sm", { hasText: "Find me mice treated with NDMA." }),
    ).toBeVisible();

    // HTTP POST should have received the query
    await delay(200);
    const queryBody = mock.receivedHttpBodies.find(
      (b: any) => b?.query === "Find me mice treated with NDMA.",
    );
    expect(queryBody).toBeTruthy();
  });
});
