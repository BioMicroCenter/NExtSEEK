import { test, expect, Page } from "@playwright/test";

// Skip guard: only run when USE_REAL_BACKEND is explicitly set
test.skip(!process.env.USE_REAL_BACKEND, "Requires USE_REAL_BACKEND=true");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Send a prompt in the chat input and wait for the assistant reply. */
async function sendAndWaitForReply(
  page: Page,
  prompt: string,
): Promise<{ replyText: string; durationMs: number }> {
  // Count existing assistant bubbles BEFORE sending so we can reliably detect
  // the newly-added assistant reply.
  const assistantBubbles = page.locator(".rounded-2xl.rounded-bl-sm");
  const countBefore = await assistantBubbles.count();

  const input = page.getByPlaceholder("Ask NExtSEEK a question...");
  await input.fill(prompt);
  await input.press("Enter");

  const t0 = performance.now();

  // Wait until at least one more assistant bubble appears (up to 120s).
  await expect(async () => {
    const countNow = await assistantBubbles.count();
    expect(countNow).toBeGreaterThan(countBefore);
  }).toPass({ timeout: 120_000, intervals: [500, 1_000, 2_000] });

  const reply = assistantBubbles.nth(countBefore); // the newly-added bubble
  await expect(reply).toBeVisible({ timeout: 5_000 });

  const replyText = (await reply.textContent()) ?? "";
  expect(replyText.trim().length).toBeGreaterThan(0);

  const durationMs = performance.now() - t0;
  return { replyText, durationMs };
}

/** Open the debug panel and collect any visible agent badges. */
async function collectDebugBadges(page: Page): Promise<string[]> {
  const badges: string[] = [];
  const agents = ["entity", "parser", "api", "http", "chatter", "reporter"] as const;

  // Best-effort: never fail the test, but also never leave the dialog open.
  try {
    const toggle = page.getByLabel("Toggle debug panel");
    if (!(await toggle.isVisible({ timeout: 1_500 }).catch(() => false))) return badges;

    await toggle.click({ timeout: 2_000 });

    const dialog = page.getByRole("dialog", { name: "Debug Output" });
    await dialog.isVisible({ timeout: 2_000 }).catch(() => {});

    for (const agent of agents) {
      const badge = dialog.getByText(agent).first();
      if (await badge.isVisible({ timeout: 300 }).catch(() => false)) badges.push(agent);
    }
  } catch {
    // ignore
  } finally {
    // Ensure we don't keep a focus-trapping dialog open between serial tests.
    await page.keyboard.press("Escape").catch(() => {});
  }

  return badges;
}

// ---------------------------------------------------------------------------
// Chain A: Tumor queries (sequential, shared page)
// ---------------------------------------------------------------------------

test.describe.serial("Chain A: Tumor queries", () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("tumor_patient_4", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Show me tumor data for patient 4.",
    );

    console.log(
      `[tumor_patient_4] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[tumor_patient_4] agents: ${badges.join(", ")}`);
  });

  test("tumor_dfci4", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Try that search again but instead with DFCI4.",
    );

    console.log(
      `[tumor_dfci4] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[tumor_dfci4] agents: ${badges.join(", ")}`);
  });
});

// ---------------------------------------------------------------------------
// Chain B: Monkey queries (sequential, shared page)
// ---------------------------------------------------------------------------

test.describe.serial("Chain B: Monkey queries", () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("what_monkeys_exist", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "What monkeys exist in the database?",
    );

    console.log(
      `[what_monkeys_exist] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[what_monkeys_exist] agents: ${badges.join(", ")}`);
  });

  test("monkeys_cd8_depleted", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Which of those monkeys are depleted of CD8?",
    );

    console.log(
      `[monkeys_cd8_depleted] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[monkeys_cd8_depleted] agents: ${badges.join(", ")}`);
  });
});

// ---------------------------------------------------------------------------
// Independent queries (11 tests, each gets its own page)
// ---------------------------------------------------------------------------

test.describe("Independent queries", () => {
  // Intentionally avoid any per-test teardown hooks here.
  // If the real backend leaves long-lived connections, Playwright's built-in
  // page/context teardown should still complete without consuming the full test
  // timeout. Any additional teardown logic risks hanging the test itself.

  test("mice_ndma", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me mice treated with NDMA.",
    );
    console.log(`[mice_ndma] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[mice_ndma] agents: ${badges.join(", ")}`);
  });

  test("monkeys_flow_and_seq", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me monkeys that have flow and sequencing data.",
    );
    console.log(`[monkeys_flow_and_seq] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[monkeys_flow_and_seq] agents: ${badges.join(", ")}`);
  });

  test("cd8_antibodies", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me all CD8 antibodies in the database.",
    );
    console.log(`[cd8_antibodies] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[cd8_antibodies] agents: ${badges.join(", ")}`);
  });

  test("sample_tree", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Show me all samples derived from CEL-250319WHI-1-PUB?",
    );
    console.log(`[sample_tree] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[sample_tree] agents: ${badges.join(", ")}`);
  });

  test("retrieval", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Retrieve all samples associated with: NHP-220630FLY-5-PUB",
    );
    console.log(`[retrieval] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[retrieval] agents: ${badges.join(", ")}`);
  });

  test("list_assays_access", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Show me all assays I have access to.",
    );
    console.log(`[list_assays_access] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[list_assays_access] agents: ${badges.join(", ")}`);
  });

  test("omero_fibrin_images", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "What fibrin images exist?",
    );
    console.log(`[omero_fibrin_images] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[omero_fibrin_images] agents: ${badges.join(", ")}`);
  });

  test("gpt_assay", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "What GPT data exists in the database?",
    );
    console.log(`[gpt_assay] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[gpt_assay] agents: ${badges.join(", ")}`);
  });

  test("reporter_impact", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "How many samples were uploaded for impact from 2023 to 2025?",
    );
    console.log(`[reporter_impact] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[reporter_impact] agents: ${badges.join(", ")}`);
  });

  test("reporter_language", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "How many samples were uploaded last month?",
    );
    console.log(`[reporter_language] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[reporter_language] agents: ${badges.join(", ")}`);
  });

  test("GEO", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.waitForSelector('text="NExtSEEK Chat"', { timeout: 15_000 });

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB",
    );
    console.log(`[GEO] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[GEO] agents: ${badges.join(", ")}`);
  });
});
