import { test, expect, Page, Download } from "@playwright/test";

// Skip guard: only run when USE_REAL_BACKEND is explicitly set
test.skip(!process.env.USE_REAL_BACKEND, "Requires USE_REAL_BACKEND=true");

const EMBEDDED_URL = "https://nextseek-dev.mit.edu/seek/salt/";

// ---------------------------------------------------------------------------
// Helpers (reused patterns from test-case-1-embedded.spec.ts)
// ---------------------------------------------------------------------------

async function ensureLoggedIn(page: Page): Promise<void> {
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

  const hasUserField = await page
    .locator(userSel)
    .first()
    .isVisible({ timeout: 2_000 })
    .catch(() => false);
  if (hasUserField) {
    await page.fill(userSel, "demo");
    await page.fill(passSel, "demopassword");
    await Promise.all([
      page
        .waitForNavigation({
          waitUntil: "domcontentloaded",
          timeout: 20_000,
        })
        .catch(() => {}),
      loginForm.locator(submitSel).first().click(),
    ]);
  }

  if (!page.url().startsWith(EMBEDDED_URL)) {
    await page.goto(EMBEDDED_URL);
    await page.waitForLoadState("domcontentloaded");
  }
}

async function navigateToAssistantTab(page: Page): Promise<void> {
  await page.goto(EMBEDDED_URL);
  await ensureLoggedIn(page);

  const root = page.locator("#chat-assistant-root");
  if (await root.isVisible({ timeout: 2_000 }).catch(() => false)) {
    return;
  }

  const candidates = [
    page.getByRole("tab", { name: /assistant/i }).first(),
    page.locator(".tabs-title", { hasText: "Assistant" }).first(),
    page.locator("span.tabs-title:has-text('Assistant')").first(),
    page.locator("a:has-text('Assistant')").first(),
  ];

  let clicked = false;
  for (const loc of candidates) {
    if (clicked) break;
    const visible = await loc
      .isVisible({ timeout: 2_000 })
      .catch(() => false);
    if (!visible) continue;
    await loc.click({ timeout: 10_000 });
    clicked = true;
  }

  if (!clicked) {
    const tabTexts = await page
      .locator(".tabs-title")
      .allTextContents()
      .catch(() => []);
    throw new Error(
      `Could not find Assistant tab. Found .tabs-title: ${JSON.stringify(tabTexts)}`,
    );
  }

  await page.waitForTimeout(250);
  await expect(root).toBeVisible({ timeout: 15_000 });
}

async function sendAndWaitForReply(
  page: Page,
  prompt: string,
): Promise<{ replyText: string; durationMs: number }> {
  const root = page.locator("#chat-assistant-root");
  const input = root.getByPlaceholder("Ask NExtSEEK a question...");
  await input.fill(prompt);
  await input.press("Enter");

  const t0 = performance.now();

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

/** Wait for a download event and return the Download object. */
async function clickAndWaitForDownload(
  page: Page,
  locator: ReturnType<Page["locator"]>,
): Promise<Download> {
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 30_000 }),
    locator.click(),
  ]);
  return download;
}

// ---------------------------------------------------------------------------
// Artifact download validation (serial — same page for both queries)
// ---------------------------------------------------------------------------

test.describe.serial("Artifact downloads (embedded)", () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    page = await context.newPage();
    await navigateToAssistantTab(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("GEO_submission_artifacts", async () => {
    test.setTimeout(180_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB",
    );
    console.log(
      `[artifact:GEO] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const root = page.locator("#chat-assistant-root");

    // Assert: ReportArtifacts container is visible in the latest reply bubble
    const artifactsSection = root.locator(".mt-3.space-y-4").last();
    await expect(artifactsSection).toBeVisible({ timeout: 10_000 });

    // Assert: at least one table is rendered inline
    const tables = artifactsSection.locator("table");
    const tableCount = await tables.count();
    console.log(`[artifact:GEO] inline tables found: ${tableCount}`);
    expect(tableCount).toBeGreaterThanOrEqual(1);

    // Assert: tables have header rows and data rows
    const firstTable = tables.first();
    const headerCells = firstTable.locator("thead th");
    expect(await headerCells.count()).toBeGreaterThan(0);
    const dataRows = firstTable.locator("tbody tr");
    expect(await dataRows.count()).toBeGreaterThan(0);

    // Assert: GEO Submission Workbook file artifact button exists
    const geoButton = artifactsSection.getByText("GEO Submission Workbook");
    await expect(geoButton).toBeVisible({ timeout: 5_000 });

    // Assert: "Download All Tables" button exists if 2+ tables
    if (tableCount >= 2) {
      const allTablesBtn = artifactsSection.getByText(
        "Download All Tables (.xlsx)",
      );
      await expect(allTablesBtn).toBeVisible({ timeout: 5_000 });
    }

    // Test per-table download
    const perTableBtn = artifactsSection
      .locator('button[aria-label^="Download"]')
      .first();
    if (await perTableBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
      const download = await clickAndWaitForDownload(page, perTableBtn);
      console.log(
        `[artifact:GEO] per-table download: ${download.suggestedFilename()}`,
      );
      expect(download.suggestedFilename()).toContain(".xlsx");
    }

    // Test GEO file download
    const geoDownload = await clickAndWaitForDownload(page, geoButton);
    console.log(
      `[artifact:GEO] GEO file download: ${geoDownload.suggestedFilename()}`,
    );
    expect(geoDownload.suggestedFilename()).toContain(".xlsx");

    // Screenshot
    await page.screenshot({
      path: "test-results/artifact-geo-submission.png",
      fullPage: false,
    });
  });

  test("reporter_impact_artifacts", async () => {
    test.setTimeout(180_000);

    const { replyText, durationMs } = await sendAndWaitForReply(
      page,
      "How many samples were uploaded for impact from 2023 to 2025?",
    );
    console.log(
      `[artifact:reporter_impact] reply length=${replyText.length}, duration=${Math.round(durationMs)}ms`,
    );

    const root = page.locator("#chat-assistant-root");

    // The reporter_impact query uses summary_sql mode, which may or may not
    // produce table artifacts depending on whether it routes through
    // report_writer_output. Check if artifacts section appeared.
    const allArtifactSections = root.locator(".mt-3.space-y-4");
    const artifactCount = await allArtifactSections.count();

    if (artifactCount > 0) {
      // At least one artifacts section — grab the last one (latest reply)
      const artifactsSection = allArtifactSections.last();

      const tables = artifactsSection.locator("table");
      const tableCount = await tables.count();
      console.log(
        `[artifact:reporter_impact] inline tables found: ${tableCount}`,
      );

      if (tableCount > 0) {
        // Verify table structure
        const firstTable = tables.first();
        const headerCells = firstTable.locator("thead th");
        expect(await headerCells.count()).toBeGreaterThan(0);
        const dataRows = firstTable.locator("tbody tr");
        expect(await dataRows.count()).toBeGreaterThan(0);

        // Test download
        const perTableBtn = artifactsSection
          .locator('button[aria-label^="Download"]')
          .first();
        if (
          await perTableBtn.isVisible({ timeout: 2_000 }).catch(() => false)
        ) {
          const download = await clickAndWaitForDownload(page, perTableBtn);
          console.log(
            `[artifact:reporter_impact] download: ${download.suggestedFilename()}`,
          );
          expect(download.suggestedFilename()).toContain(".xlsx");
        }
      }
    } else {
      console.log(
        `[artifact:reporter_impact] No artifacts section found — summary_sql mode may not produce table artifacts. This is expected for this query type.`,
      );
    }

    // Screenshot
    await page.screenshot({
      path: "test-results/artifact-reporter-impact.png",
      fullPage: false,
    });
  });
});
