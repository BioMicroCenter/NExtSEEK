import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe("Dark Mode", () => {
  test("toggles dark class on html element", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Initially should not have dark class (localStorage is empty)
    const hasDarkBefore = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    );
    expect(hasDarkBefore).toBe(false);

    // Click dark mode toggle
    await page.getByLabel("Toggle dark mode").click();

    const hasDarkAfter = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    );
    expect(hasDarkAfter).toBe(true);
  });

  test("persists dark mode preference across reload", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Enable dark mode
    await page.getByLabel("Toggle dark mode").click();

    // Verify localStorage was set
    const theme = await page.evaluate(() => localStorage.getItem("theme"));
    expect(theme).toBe("dark");

    // Re-setup mocks for the reload (routes are cleared on navigation)
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Dark class should be restored from localStorage
    const hasDark = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    );
    expect(hasDark).toBe(true);
  });
});
