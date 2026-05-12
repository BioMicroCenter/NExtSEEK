import { test, expect, Page } from "@playwright/test";

// Skip guard: only run when USE_REAL_BACKEND is explicitly set
test.skip(!process.env.USE_REAL_BACKEND, "Requires USE_REAL_BACKEND=true");

// Integrated (embedded) assistant lives under the Django app (/seek/ prefix).
const EMBEDDED_URL = "https://nextseek-dev.mit.edu/seek/salt/";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Log in via Django login form if redirected. */
async function ensureLoggedIn(page: Page): Promise<void> {
  // Always perform a fresh login navigation to ensure an authenticated session.
  // This is more reliable than trying to infer auth state from intermediate pages.
  const loginUrl = new URL(
    `/login/?next=${encodeURIComponent(new URL(EMBEDDED_URL).pathname)}`,
    EMBEDDED_URL,
  ).toString();

  await page.goto(loginUrl);
  await page.waitForLoadState("domcontentloaded");

  const userSel = 'input[name="username"], #id_username, #username';
  const passSel = 'input[name="password"], #id_password, #password';
  const loginForm = page.locator(`form:has(${userSel})`).first();
  const submitSel = 'button[type="submit"], input[type="submit"]';

  const hasUserField = await page.locator(userSel).first().isVisible({ timeout: 2_000 }).catch(() => false);
  if (hasUserField) {
    await page.fill(userSel, "demo");
    await page.fill(passSel, "demopassword");
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20_000 }).catch(() => {}),
      loginForm.locator(submitSel).first().click(),
    ]);
  }

  // Ensure we land on the embedded assistant page after login.
  if (!page.url().startsWith(EMBEDDED_URL)) {
    await page.goto(EMBEDDED_URL);
    await page.waitForLoadState("domcontentloaded");
  }
}

/** Navigate to the embedded app and activate the Assistant tab. */
async function navigateToAssistantTab(page: Page): Promise<void> {
  await page.goto(EMBEDDED_URL);
  await ensureLoggedIn(page);

  // If the chat root is already visible, we don't need tab switching.
  const root = page.locator("#chat-assistant-root");
  if (await root.isVisible({ timeout: 2_000 }).catch(() => false)) {
    return;
  }

  // Click the "Assistant" tab. The integrated UI markup differs across deployments,
  // so try a few common EasyUI/tab patterns.
  const candidates = [
    page.getByRole("tab", { name: /assistant/i }).first(),
    page.locator(".tabs-title", { hasText: "Assistant" }).first(),
    page.locator("span.tabs-title:has-text('Assistant')").first(),
    page.locator("a:has-text('Assistant')").first(),
  ];

  let clicked = false;
  for (const loc of candidates) {
    if (clicked) break;
    const visible = await loc.isVisible({ timeout: 2_000 }).catch(() => false);
    if (!visible) continue;
    await loc.click({ timeout: 10_000 });
    clicked = true;
  }

  if (!clicked) {
    // Provide minimal diagnostics in failure traces.
    const tabTexts = await page
      .locator(".tabs-title")
      .allTextContents()
      .catch(() => []);
    throw new Error(
      `Could not find Assistant tab. Found .tabs-title: ${JSON.stringify(tabTexts)}`,
    );
  }

  await page.waitForTimeout(250);

  // Wait for the chat container to mount
  await expect(root).toBeVisible({
    timeout: 15_000,
  });
}

/** Send a prompt and wait for the assistant reply within the embedded container. */
async function sendAndWaitForReply(
  page: Page,
  prompt: string,
): Promise<{ replyText: string; durationMs: number }> {
  const root = page.locator("#chat-assistant-root");
  const input = root.getByPlaceholder("Ask NExtSEEK a question...");
  await input.fill(prompt);
  await input.press("Enter");

  const t0 = performance.now();

  // Wait for a NEW assistant bubble within the embedded container.
  const assistantBubbles = root.locator(".rounded-2xl.rounded-bl-sm");
  const countBefore = await assistantBubbles.count();

  await expect(async () => {
    const countNow = await assistantBubbles.count();
    expect(countNow).toBeGreaterThan(countBefore);
  }).toPass({ timeout: 120_000, intervals: [500, 1_000, 2_000] });

  const reply = assistantBubbles.nth(countBefore);
  await expect(reply).toBeVisible({ timeout: 5_000 });

  const replyText = (await reply.textContent()) ?? "";
  expect(replyText.trim().length).toBeGreaterThan(0);

  const durationMs = performance.now() - t0;
  return { replyText, durationMs };
}

/** Open the debug panel within the embedded container and collect agent badges. */
async function collectDebugBadges(page: Page): Promise<string[]> {
  const badges: string[] = [];
  const agents = ["entity", "parser", "api", "http", "chatter", "reporter"] as const;

  // Best-effort: never fail the test, and never hang or leave the panel open.
  try {
    const root = page.locator("#chat-assistant-root");
    const toggle = root.getByLabel("Toggle debug panel");
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
    await page.keyboard.press("Escape").catch(() => {});
  }

  return badges;
}

// ---------------------------------------------------------------------------
// Chain A: Tumor queries (sequential, shared page)
// ---------------------------------------------------------------------------

test.describe.serial("Chain A: Tumor queries (embedded)", () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    page = await context.newPage();
    await navigateToAssistantTab(page);
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
      `[embedded:tumor_patient_4] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:tumor_patient_4] agents: ${badges.join(", ")}`);
  });

  test("tumor_dfci4", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Try that search again but instead with DFCI4.",
    );
    console.log(
      `[embedded:tumor_dfci4] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:tumor_dfci4] agents: ${badges.join(", ")}`);
  });
});

// ---------------------------------------------------------------------------
// Chain B: Monkey queries (sequential, shared page)
// ---------------------------------------------------------------------------

test.describe.serial("Chain B: Monkey queries (embedded)", () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    page = await context.newPage();
    await navigateToAssistantTab(page);
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
      `[embedded:what_monkeys_exist] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:what_monkeys_exist] agents: ${badges.join(", ")}`);
  });

  test("monkeys_cd8_depleted", async () => {
    test.setTimeout(120_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Which of those monkeys are depleted of CD8?",
    );
    console.log(
      `[embedded:monkeys_cd8_depleted] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:monkeys_cd8_depleted] agents: ${badges.join(", ")}`);
  });
});

// ---------------------------------------------------------------------------
// Independent queries (11 tests, each gets its own page)
// ---------------------------------------------------------------------------

test.describe("Independent queries (embedded)", () => {
  test.beforeEach(async ({ page }) => {
    await navigateToAssistantTab(page);
  });

  test("mice_ndma", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me mice treated with NDMA.",
    );
    console.log(`[embedded:mice_ndma] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:mice_ndma] agents: ${badges.join(", ")}`);
  });

  test("monkeys_flow_and_seq", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me monkeys that have flow and sequencing data.",
    );
    console.log(`[embedded:monkeys_flow_and_seq] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:monkeys_flow_and_seq] agents: ${badges.join(", ")}`);
  });

  test("cd8_antibodies", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Find me all CD8 antibodies in the database.",
    );
    console.log(`[embedded:cd8_antibodies] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:cd8_antibodies] agents: ${badges.join(", ")}`);
  });

  test("sample_tree", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Show me all samples derived from CEL-250319WHI-1-PUB?",
    );
    console.log(`[embedded:sample_tree] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:sample_tree] agents: ${badges.join(", ")}`);
  });

  test("retrieval", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Retrieve all samples associated with: NHP-220630FLY-5-PUB",
    );
    console.log(`[embedded:retrieval] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:retrieval] agents: ${badges.join(", ")}`);
  });

  test("list_assays_access", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Show me all assays I have access to.",
    );
    console.log(`[embedded:list_assays_access] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:list_assays_access] agents: ${badges.join(", ")}`);
  });

  test("omero_fibrin_images", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "What fibrin images exist?",
    );
    console.log(`[embedded:omero_fibrin_images] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:omero_fibrin_images] agents: ${badges.join(", ")}`);
  });

  test("gpt_assay", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "What GPT data exists in the database?",
    );
    console.log(`[embedded:gpt_assay] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:gpt_assay] agents: ${badges.join(", ")}`);
  });

  test("reporter_impact", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "How many samples were uploaded for impact from 2023 to 2025?",
    );
    console.log(`[embedded:reporter_impact] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:reporter_impact] agents: ${badges.join(", ")}`);
  });

  test("reporter_language", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "How many samples were uploaded last month?",
    );
    console.log(`[embedded:reporter_language] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:reporter_language] agents: ${badges.join(", ")}`);
  });

  test("GEO", async ({ page }) => {
    test.setTimeout(120_000);
    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB",
    );
    console.log(`[embedded:GEO] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`);
    const badges = await collectDebugBadges(page);
    if (badges.length) console.log(`[embedded:GEO] agents: ${badges.join(", ")}`);
  });
});
