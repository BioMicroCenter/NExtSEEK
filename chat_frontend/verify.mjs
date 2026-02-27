import { chromium } from "playwright";

const BASE = "https://nextseek-dev.mit.edu";
const DIR = "/tmp/demo-day-verify";

async function ensureLoggedIn(page) {
  const loginUrl = `${BASE}/login/?next=${encodeURIComponent("/seek/assistant/")}`;
  await page.goto(loginUrl);
  await page.waitForLoadState("domcontentloaded");
  const userSel = 'input[name="username"], #id_username, #username';
  const passSel = 'input[name="password"], #id_password, #password';
  const loginForm = page.locator(`form:has(${userSel})`).first();
  const submitSel = 'button[type="submit"], input[type="submit"]';
  const hasUser = await page.locator(userSel).first().isVisible({ timeout: 5000 }).catch(() => false);
  if (hasUser) {
    await page.fill(userSel, "demo");
    await page.fill(passSel, "demopassword");
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {}),
      loginForm.locator(submitSel).first().click(),
    ]);
  }
  if (!page.url().includes("/seek/assistant")) {
    await page.goto(`${BASE}/seek/assistant/`);
    await page.waitForLoadState("domcontentloaded");
  }
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  console.log("=== VERIFICATION RUN ===\n");

  await ensureLoggedIn(page);
  await page.waitForTimeout(3000);

  const root = page.locator("#chat-assistant-root");
  let rootVisible = await root.isVisible({ timeout: 5000 }).catch(() => false);
  if (!rootVisible) {
    const tab = page.locator("span.tabs-title:has-text('Assistant')").first();
    if (await tab.isVisible({ timeout: 2000 }).catch(() => false)) await tab.click();
    await page.waitForTimeout(1500);
    rootVisible = await root.isVisible({ timeout: 5000 }).catch(() => false);
  }

  if (!rootVisible) {
    console.log("FAIL: Chat root not visible");
    await page.screenshot({ path: `${DIR}/FAIL-no-root.png` });
    await browser.close();
    return;
  }

  // CHECK 1: Send button size
  const sendBtn = root.locator('button[aria-label="Send message"]');
  const btnBox = await sendBtn.boundingBox();
  const btnStyles = await sendBtn.evaluate(el => {
    const cs = getComputedStyle(el);
    return { width: cs.width, height: cs.height };
  });
  const svgStyles = await sendBtn.locator("svg").evaluate(el => {
    const cs = getComputedStyle(el);
    return { width: cs.width, height: cs.height };
  });
  console.log(`CHECK 1 - Send button size: ${btnStyles.width} x ${btnStyles.height} (target: 40px x 40px)`);
  console.log(`  Icon size: ${svgStyles.width} x ${svgStyles.height} (target: 20px x 20px)`);
  console.log(`  ${btnBox?.width === 40 ? "PASS" : "FAIL"}`);

  const inputArea = root.locator(".border-t").last();
  await inputArea.screenshot({ path: `${DIR}/01-send-button.png` });

  // CHECK 2: Submit query and verify stepper auto-expands
  const input = root.getByPlaceholder("Ask NExtSEEK a question...");
  await input.fill("What tumor samples do we have?");
  await input.press("Enter");
  console.log("\nQuery submitted, checking stepper...");
  await page.waitForTimeout(2000);

  // Check if expanded step list is visible (not just the summary bar)
  const expandedSteps = root.locator(".border-b .border-t .space-y-1");
  const stepperExpanded = await expandedSteps.isVisible({ timeout: 3000 }).catch(() => false);
  console.log(`CHECK 2 - Stepper auto-expanded: ${stepperExpanded ? "PASS" : "FAIL"}`);
  await page.screenshot({ path: `${DIR}/02-stepper-expanded.png` });

  // Wait for response
  console.log("\nWaiting for response...");
  const bubbles = root.locator(".rounded-2xl.rounded-bl-sm");
  try {
    await bubbles.first().waitFor({ state: "visible", timeout: 120000 });
    await page.waitForTimeout(3000);
  } catch {
    console.log("Timeout waiting for response");
  }

  await page.screenshot({ path: `${DIR}/03-response-top.png` });

  // CHECK 3: "NExtSEEK search summary" should NOT be in the bubble
  const bubbleText = await bubbles.first().textContent() ?? "";
  const hasSummaryInBubble = bubbleText.includes("NExtSEEK search summary");
  const hasPreviewInBubble = bubbleText.includes("API request preview");
  console.log(`\nCHECK 3a - "NExtSEEK search summary" stripped from bubble: ${!hasSummaryInBubble ? "PASS" : "FAIL"}`);
  console.log(`CHECK 3b - "API request preview" stripped from bubble: ${!hasPreviewInBubble ? "PASS" : "FAIL"}`);

  // Scroll to bottom to see controls
  const scroll = root.locator(".overflow-y-auto").first();
  await scroll.evaluate(el => el.scrollTop = el.scrollHeight);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${DIR}/04-response-bottom.png` });

  // CHECK 4: Search Details and Download Results on same line
  const searchDetailsBtn = root.locator("button:has-text('Search Details')").first();
  const downloadBtn = root.locator("button:has-text('Download Results')").first();
  const sdVisible = await searchDetailsBtn.isVisible({ timeout: 3000 }).catch(() => false);
  const dlVisible = await downloadBtn.isVisible({ timeout: 2000 }).catch(() => false);

  let sameRow = false;
  if (sdVisible && dlVisible) {
    const sdBox = await searchDetailsBtn.boundingBox();
    const dlBox = await downloadBtn.boundingBox();
    if (sdBox && dlBox) {
      // Same row if Y positions are close (within 10px)
      sameRow = Math.abs(sdBox.y - dlBox.y) < 10;
      console.log(`\nCHECK 4 - Buttons inline: SearchDetails y=${sdBox.y.toFixed(0)}, Download y=${dlBox.y.toFixed(0)} → ${sameRow ? "PASS" : "FAIL"}`);
    }
  } else {
    console.log(`\nCHECK 4 - SearchDetails visible: ${sdVisible}, Download visible: ${dlVisible}`);
  }

  // CHECK 5: Click Search Details and verify extracted sections appear inside
  if (sdVisible) {
    await searchDetailsBtn.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${DIR}/05-search-details-open.png` });

    const detailsPanel = root.locator(".rounded-lg.border.border-border\\/60").first();
    const panelText = await detailsPanel.textContent().catch(() => "");
    const summaryInPanel = panelText.includes("Endpoint") || panelText.includes("search summary");
    const previewInPanel = panelText.includes("queryParameters") || panelText.includes("requestBody");
    console.log(`CHECK 5a - Search summary in details panel: ${summaryInPanel ? "PASS" : "FAIL"}`);
    console.log(`CHECK 5b - API preview in details panel: ${previewInPanel ? "PASS" : "FAIL"}`);
  }

  await page.screenshot({ path: `${DIR}/06-final.png` });

  console.log("\n=== VERIFICATION COMPLETE ===");
  await browser.close();
}

run().catch(console.error);
