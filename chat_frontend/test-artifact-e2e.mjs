/**
 * Manual E2E validation script for artifact downloads.
 * Runs headless Chromium, logs in, sends reporter queries,
 * validates inline tables and downloads, captures screenshots.
 *
 * Usage: node test-artifact-e2e.mjs
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "fs";

const EMBEDDED_URL = "https://nextseek-dev.mit.edu/seek/salt/";
const RESULTS_DIR = "/tmp/artifact-e2e-results";

mkdirSync(RESULTS_DIR, { recursive: true });

async function ensureLoggedIn(page) {
  const loginUrl = new URL(
    `/login/?next=${encodeURIComponent(new URL(EMBEDDED_URL).pathname)}`,
    EMBEDDED_URL,
  ).toString();

  console.log(`  Going to login page: ${loginUrl}`);
  await page.goto(loginUrl);
  await page.waitForLoadState("domcontentloaded");
  console.log(`  Current URL after login page load: ${page.url()}`);
  await page.screenshot({ path: `${RESULTS_DIR}/00-login-page.png` });

  const userSel = 'input[name="username"], #id_username, #username';
  const passSel = 'input[name="password"], #id_password, #password';

  const hasUserField = await page.locator(userSel).first().isVisible({ timeout: 5_000 }).catch(() => false);
  console.log(`  Login form visible: ${hasUserField}`);

  if (hasUserField) {
    await page.fill(userSel, "demo");
    await page.fill(passSel, "demopassword");
    const loginForm = page.locator(`form:has(${userSel})`).first();
    const submitSel = 'button[type="submit"], input[type="submit"]';
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20_000 }).catch(() => {}),
      loginForm.locator(submitSel).first().click(),
    ]);
    console.log(`  URL after login submit: ${page.url()}`);
    await page.screenshot({ path: `${RESULTS_DIR}/00b-after-login.png` });
  }

  if (!page.url().startsWith(EMBEDDED_URL)) {
    console.log(`  Navigating to embedded URL...`);
    await page.goto(EMBEDDED_URL);
    await page.waitForLoadState("domcontentloaded");
    console.log(`  URL: ${page.url()}`);
  }
}

async function navigateToAssistantTab(page) {
  await ensureLoggedIn(page);

  await page.screenshot({ path: `${RESULTS_DIR}/00c-salt-page.png` });

  // Check if chat root is already visible
  const root = page.locator("#chat-assistant-root");
  if (await root.isVisible({ timeout: 3_000 }).catch(() => false)) {
    console.log("  Chat root already visible");
    return;
  }

  // List all tab titles for debugging
  const tabTexts = await page.locator(".tabs-title").allTextContents().catch(() => []);
  console.log(`  Tab titles found: ${JSON.stringify(tabTexts)}`);

  // Also check for EasyUI tab headers
  const tabHeaders = await page.locator(".tabs-header li").allTextContents().catch(() => []);
  console.log(`  Tab header li texts: ${JSON.stringify(tabHeaders)}`);

  // Try broader selectors
  const allTabs = await page.locator("[class*=tab]").allTextContents().catch(() => []);
  console.log(`  All *tab* elements (first 10): ${JSON.stringify(allTabs.slice(0, 10))}`);

  const candidates = [
    page.getByRole("tab", { name: /assistant/i }).first(),
    page.locator(".tabs-title", { hasText: "Assistant" }).first(),
    page.locator("span.tabs-title:has-text('Assistant')").first(),
    page.locator("a:has-text('Assistant')").first(),
    page.locator("li:has-text('Assistant')").first(),
  ];

  let clicked = false;
  for (const loc of candidates) {
    const visible = await loc.isVisible({ timeout: 1_500 }).catch(() => false);
    if (visible) {
      console.log(`  Clicking tab candidate...`);
      await loc.click({ timeout: 10_000 });
      clicked = true;
      break;
    }
  }

  if (!clicked) {
    console.log("  WARNING: Could not find Assistant tab to click");
  }

  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${RESULTS_DIR}/00d-after-tab-click.png` });

  const visible = await root.isVisible({ timeout: 15_000 }).catch(() => false);
  if (!visible) {
    // Dump page HTML for debugging
    const bodyHTML = await page.evaluate(() => document.body.innerHTML.substring(0, 3000));
    console.log(`  Page body (first 3000 chars):\n${bodyHTML}`);
    throw new Error("Chat root #chat-assistant-root not visible after 15s");
  }
}

async function sendAndWaitForReply(page, prompt) {
  const root = page.locator("#chat-assistant-root");
  const input = root.getByPlaceholder("Ask NExtSEEK a question...");
  await input.fill(prompt);
  await input.press("Enter");

  const t0 = performance.now();
  const bubbles = root.locator(".rounded-2xl.rounded-bl-sm");
  const countBefore = await bubbles.count();

  // Poll for new bubble (up to 120s)
  for (let i = 0; i < 240; i++) {
    await page.waitForTimeout(500);
    const countNow = await bubbles.count();
    if (countNow > countBefore) break;
  }

  const reply = bubbles.nth(countBefore);
  const text = (await reply.textContent({ timeout: 120_000 })) ?? "";
  const ms = Math.round(performance.now() - t0);
  return { text, ms };
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();

  try {
    console.log("Step 1: Navigate to Assistant tab...");
    await navigateToAssistantTab(page);
    await page.screenshot({ path: `${RESULTS_DIR}/01-assistant-tab.png` });
    console.log("✓ Logged in and on Assistant tab\n");

    // --- GEO Submission ---
    console.log("Step 2: Sending GEO Submission query...");
    const geo = await sendAndWaitForReply(
      page,
      "Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB",
    );
    console.log(`  Reply length: ${geo.text.length}, duration: ${geo.ms}ms`);
    await page.screenshot({ path: `${RESULTS_DIR}/02-geo-reply.png`, fullPage: false });

    const root = page.locator("#chat-assistant-root");

    // Check for artifacts section
    const artifactsSection = root.locator(".mt-3.space-y-4").last();
    const hasArtifacts = await artifactsSection.isVisible({ timeout: 5_000 }).catch(() => false);
    console.log(`  Artifacts section visible: ${hasArtifacts}`);

    if (hasArtifacts) {
      const tables = artifactsSection.locator("table");
      const tableCount = await tables.count();
      console.log(`  Inline full tables: ${tableCount} (expected 0 for GEO)`);

      // Check for GEO Report Preview button (collapsed)
      const previewBtn = artifactsSection.getByText("GEO Report Preview");
      const hasPreviewBtn = await previewBtn.isVisible({ timeout: 2_000 }).catch(() => false);
      console.log(`  GEO Report Preview button: ${hasPreviewBtn}`);

      // Check for GEO workbook download button
      const geoBtn = artifactsSection.getByText("GEO Submission Workbook");
      const hasGeoBtn = await geoBtn.isVisible({ timeout: 2_000 }).catch(() => false);
      console.log(`  GEO Workbook button: ${hasGeoBtn}`);

      // Check NO "Download All Tables" button (GEO uses preview, not inline tables)
      const allBtn = artifactsSection.getByText("Download All Tables (.xlsx)");
      const hasAllBtn = await allBtn.isVisible({ timeout: 2_000 }).catch(() => false);
      console.log(`  Download All button: ${hasAllBtn} (expected false for GEO)`);

      // Check NO legacy "Download Results" button
      const legacyBtn = root.getByText("Download Results");
      const hasLegacyBtn = await legacyBtn.isVisible({ timeout: 2_000 }).catch(() => false);
      console.log(`  Legacy "Download Results" button: ${hasLegacyBtn} (expected false)`);

      // Test GEO file download
      if (hasGeoBtn) {
        const [geoDownload] = await Promise.all([
          page.waitForEvent("download", { timeout: 30_000 }).catch(() => null),
          geoBtn.click(),
        ]);
        if (geoDownload) {
          console.log(`  ✓ GEO file download: ${geoDownload.suggestedFilename()}`);
        } else {
          console.log(`  ✗ GEO file download did not trigger`);
        }
      }

      // Test expanding preview
      if (hasPreviewBtn) {
        await previewBtn.click();
        await page.waitForTimeout(500);
        const previewTables = artifactsSection.locator("table");
        const previewTableCount = await previewTables.count();
        console.log(`  Preview tables after expand: ${previewTableCount}`);
        await page.screenshot({ path: `${RESULTS_DIR}/03b-geo-preview-expanded.png`, fullPage: false });
      }

      await page.screenshot({ path: `${RESULTS_DIR}/03-geo-artifacts.png`, fullPage: false });
    } else {
      console.log("  ✗ No artifacts section found in GEO reply");
      await page.screenshot({ path: `${RESULTS_DIR}/03-geo-no-artifacts.png`, fullPage: false });
    }

    // --- Reporter Impact ---
    console.log("\nStep 3: Sending Reporter Impact query...");
    const impact = await sendAndWaitForReply(
      page,
      "How many samples were uploaded for impact from 2023 to 2025?",
    );
    console.log(`  Reply length: ${impact.text.length}, duration: ${impact.ms}ms`);
    await page.screenshot({ path: `${RESULTS_DIR}/04-impact-reply.png`, fullPage: false });

    // Check artifacts in latest reply
    const impactArtifacts = root.locator(".mt-3.space-y-4").last();
    const hasImpactArtifacts = await impactArtifacts.isVisible({ timeout: 5_000 }).catch(() => false);
    console.log(`  Artifacts section visible: ${hasImpactArtifacts}`);

    if (hasImpactArtifacts) {
      const tables = impactArtifacts.locator("table");
      const tableCount = await tables.count();
      console.log(`  Inline tables: ${tableCount}`);

      if (tableCount > 0) {
        // Check table headers match Streamlit format
        const headers = await tables.first().locator("thead th").allTextContents();
        console.log(`  First table headers: ${headers.join(", ")}`);

        // Test per-table download
        const dlBtn = impactArtifacts.locator('button[aria-label^="Download"]').first();
        if (await dlBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
          const [download] = await Promise.all([
            page.waitForEvent("download", { timeout: 30_000 }).catch(() => null),
            dlBtn.click(),
          ]);
          if (download) {
            console.log(`  ✓ Impact per-table download: ${download.suggestedFilename()}`);
          } else {
            console.log(`  ✗ Impact per-table download did not trigger`);
          }
        }

        // Test Download All Tables button
        const allBtn = impactArtifacts.getByText("Download All Tables (.xlsx)");
        const hasAllBtn = await allBtn.isVisible({ timeout: 2_000 }).catch(() => false);
        console.log(`  Download All Tables button: ${hasAllBtn}`);
        if (hasAllBtn) {
          const [allDownload] = await Promise.all([
            page.waitForEvent("download", { timeout: 30_000 }).catch(() => null),
            allBtn.click(),
          ]);
          if (allDownload) {
            console.log(`  ✓ Impact Download All Tables: ${allDownload.suggestedFilename()}`);
          } else {
            console.log(`  ✗ Impact Download All Tables did not trigger`);
          }
        }
      }
      await page.screenshot({ path: `${RESULTS_DIR}/05-impact-artifacts.png`, fullPage: false });
    } else {
      console.log("  Note: summary_sql mode may not produce table artifacts (expected for this mode)");
      await page.screenshot({ path: `${RESULTS_DIR}/05-impact-no-artifacts.png`, fullPage: false });
    }

    // --- Final check: no legacy "Download Results" button anywhere ---
    const legacyBtns = root.getByText("Download Results");
    const legacyCount = await legacyBtns.count();
    console.log(`\nLegacy "Download Results" buttons on page: ${legacyCount} (expected 0)`);

    console.log(`\n✓ All screenshots saved to ${RESULTS_DIR}/`);
  } catch (err) {
    console.error("ERROR:", err.message);
    await page.screenshot({ path: `${RESULTS_DIR}/error.png` }).catch(() => {});
  } finally {
    await browser.close();
  }
})();
