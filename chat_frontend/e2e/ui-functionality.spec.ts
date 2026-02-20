import { test, expect } from "@playwright/test";
import { setupMocks, delay } from "./fixtures/ws-mock";

test.describe("UI Functionality", () => {
  // --- 1. Left sidebar opens/closes ---
  test("left sidebar opens and closes", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Open left sidebar
    await page.getByLabel("Toggle tests panel").click();
    await delay(300);

    // Should see test case content
    await expect(page.getByText("mice_ndma")).toBeVisible();

    // Close by pressing Escape
    await page.keyboard.press("Escape");
    await delay(300);

    // Content should no longer be visible
    await expect(page.getByText("mice_ndma")).not.toBeVisible();
  });

  // --- 2. Right sidebar opens/closes ---
  // --- 3. Test case list populates ---
  test("test case list populates with mock data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(500);

    // Open left sidebar
    await page.getByLabel("Toggle tests panel").click();
    await delay(300);

    // Both mock test cases should be visible
    await expect(page.getByText("mice_ndma")).toBeVisible();
    await expect(page.getByText("cd8_antibodies")).toBeVisible();
  });

  // --- 4. Run All button sends staggered queries ---
  test("Run All button sends staggered queries", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(500);

    // Open left sidebar
    await page.getByLabel("Toggle tests panel").click();
    await delay(300);

    // Click Run All
    await page.getByRole("button", { name: "Run All" }).click();
    await delay(2000); // Allow staggered queries to fire

    // At least 2 user message bubbles should appear (mock has 2 test cases)
    const userBubbles = page.locator(".rounded-2xl.rounded-br-sm");
    await expect(userBubbles).toHaveCount(2, { timeout: 5_000 });
  });

  // --- 5. Processing stepper shows correct agents for new_search mode ---
  test("processing stepper shows correct agents for new_search mode", async ({
    page,
  }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Send a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("Find me mice treated with NDMA");
    await input.press("Enter");
    await delay(300);

    // Simulate the full new_search agent pipeline
    mock.simulateAgentStarted("entity", "");
    await delay(50);
    await expect(page.getByTitle("Extracting entities")).toBeVisible();

    mock.simulateAgentComplete("entity", "Found: TIS");
    mock.simulateAgentStarted("parser", "");
    await delay(50);
    await expect(page.getByTitle("Planning query")).toBeVisible();

    mock.simulateAgentComplete("parser", "mode=new_search");
    mock.simulateAgentStarted("api", "new_search");
    await delay(50);
    await expect(page.getByTitle("Building request")).toBeVisible();

    mock.simulateAgentComplete("api", "POST /samples/advanced_search/");
    mock.simulateAgentStarted("http", "new_search");
    await delay(50);
    await expect(page.getByTitle("Executing search")).toBeVisible();

    mock.simulateAgentComplete("http", "200 OK, 42 results");
    mock.simulateAgentStarted("chatter", "new_search");
    await delay(50);
    await expect(page.getByTitle("Summarizing results")).toBeVisible();

    // Verify entity step is still visible in the stepper
    await expect(page.getByTitle("Extracting entities")).toBeVisible();
  });

  // --- 6. Processing stepper shows reporter mode steps ---
  test("processing stepper shows reporter mode steps", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Send a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("How many samples were uploaded for impact?");
    await input.press("Enter");
    await delay(300);

    // Simulate reporter pipeline
    mock.simulateAgentStarted("entity", "");
    await delay(50);
    mock.simulateAgentComplete("entity", "Found: project=IMPACT");
    mock.simulateAgentStarted("parser", "");
    await delay(50);
    mock.simulateAgentComplete("parser", "mode=reporter");
    mock.simulateAgentStarted("reporter", "reporter");
    await delay(100);

    // Reporter step should be visible
    await expect(page.getByTitle("Running report")).toBeVisible();

    // Entity step should also still be visible
    await expect(page.getByTitle("Extracting entities")).toBeVisible();
  });

  // --- 7. Message input auto-resizes ---
  test("message input auto-resizes on multi-line text", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const textarea = page.getByPlaceholder("Ask NExtSEEK a question...");

    // Get initial height
    const initialHeight = await textarea.evaluate(
      (el) => (el as HTMLTextAreaElement).offsetHeight,
    );

    // Type multi-line text to trigger auto-resize
    await textarea.fill("Line one\nLine two\nLine three\nLine four\nLine five");
    await delay(100);

    // Dispatch input event to trigger auto-resize hook
    await textarea.dispatchEvent("input");
    await delay(200);

    const newHeight = await textarea.evaluate(
      (el) => (el as HTMLTextAreaElement).offsetHeight,
    );

    // Height should have increased
    expect(newHeight).toBeGreaterThan(initialHeight);
  });

  // --- 8. Message input disabled during query ---
  test("message input is disabled during query and re-enabled after", async ({
    page,
  }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");

    // Input should be enabled initially
    await expect(input).toBeEnabled();

    // Fill and submit a query
    await input.fill("test query");
    await input.press("Enter");
    await delay(300);

    // Input should be disabled while processing
    await expect(input).toBeDisabled();

    // Simulate pipeline completion
    mock.simulateAgentStarted("entity", "");
    await delay(50);
    mock.simulateAgentComplete("entity", "test");
    mock.simulateQueryComplete("Done.", 1);
    await delay(300);

    // Input should be re-enabled after completion
    await expect(input).toBeEnabled();
  });

  // --- 9. Shift+Enter creates newline (does not send) ---
  test("Shift+Enter creates newline without sending", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const input = page.getByPlaceholder("Ask NExtSEEK a question...");

    // Focus and type some text
    await input.click();
    await input.type("Hello world");
    await delay(100);

    // Press Shift+Enter (should NOT send)
    await input.press("Shift+Enter");
    await delay(300);

    // No user message bubble should have appeared
    const userBubbles = page.locator(".rounded-2xl.rounded-br-sm");
    await expect(userBubbles).toHaveCount(0);

    // Input should still contain the text
    const value = await input.inputValue();
    expect(value).toContain("Hello world");
  });

  // --- 10. Empty input cannot send ---
  test("empty input cannot send", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    const sendButton = page.getByLabel("Send message");

    // Send button should be disabled when input is empty
    await expect(sendButton).toBeDisabled();

    // Fill input with text
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("some text");
    await delay(100);

    // Send button should be enabled
    await expect(sendButton).toBeEnabled();

    // Clear the input
    await input.fill("");
    await delay(100);

    // Send button should be disabled again
    await expect(sendButton).toBeDisabled();
  });

  // --- 11. Auto-scroll on new messages ---
  test("auto-scrolls to bottom on new messages", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // The scroll container is the MessageList div
    const scrollContainer = page.locator("div.flex-1.overflow-y-auto").first();

    // Send several messages with responses to fill the chat area
    for (let i = 0; i < 5; i++) {
      const input = page.getByPlaceholder("Ask NExtSEEK a question...");
      await input.fill(`Test query number ${i + 1}`);
      await input.press("Enter");
      await delay(300);

      // Simulate quick pipeline completion
      mock.simulateAgentStarted("entity", "");
      await delay(50);
      mock.simulateAgentComplete("entity", "test");
      mock.simulateQueryComplete(
        `This is a long response to query ${i + 1}. `.repeat(10),
        i + 1,
      );
      await delay(300);
    }

    // Check that scroll position is near the bottom
    const scrollInfo = await scrollContainer.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }));

    const distanceFromBottom =
      scrollInfo.scrollHeight - scrollInfo.scrollTop - scrollInfo.clientHeight;
    // Should be within 150px of the bottom
    expect(distanceFromBottom).toBeLessThan(150);
  });

  // --- 12. Error message styling ---
  test("error message appears with correct styling", async ({ page }) => {
    const mock = await setupMocks(page);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 10_000 });
    await delay(200);

    // Send a query
    const input = page.getByPlaceholder("Ask NExtSEEK a question...");
    await input.fill("bad query");
    await input.press("Enter");
    await delay(300);

    // Simulate error
    mock.simulateAgentStarted("entity", "");
    await delay(50);
    mock.simulateQueryError("Pipeline failed: connection timeout");
    await delay(300);

    // Error message should be visible
    const errorMessage = page.getByText(
      "Error: Pipeline failed: connection timeout",
    );
    await expect(errorMessage).toBeVisible();

    // Error message should be styled as italic (system message)
    const isItalic = await errorMessage.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return style.fontStyle === "italic";
    });
    expect(isItalic).toBe(true);
  });
});
