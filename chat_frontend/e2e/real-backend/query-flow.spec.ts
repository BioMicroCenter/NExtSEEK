import { test, expect } from "@playwright/test";

// These tests only run when USE_REAL_BACKEND=true
// They require VITE_API_USER and VITE_API_PASS environment variables
// set in frontend/.env

test.describe("Real Backend Query Flow", () => {
  test.skip(!process.env.USE_REAL_BACKEND, "Requires USE_REAL_BACKEND=true");

  test("boots into chat and sends a simple query", async ({ page }) => {
    test.setTimeout(120_000);

    await page.goto("/");

    // App boots directly into chat (no login form)
    await expect(
      page.locator("header").getByText("NExtSEEK Chat"),
    ).toBeVisible({ timeout: 15_000 });

    // Send a simple query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("Find me mice treated with NDMA");
    await input.press("Enter");

    // Wait for response (long timeout for real backend)
    await expect(page.locator(".rounded-2xl.rounded-bl-sm").first()).toBeVisible({
      timeout: 120_000,
    });

    // Open debug panel and verify entries
    await page.getByLabel("Toggle debug panel").click();
    await expect(page.getByText("entity").first()).toBeVisible({
      timeout: 5_000,
    });
  });
});
