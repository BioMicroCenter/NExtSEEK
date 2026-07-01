import { chromium } from "playwright";
import crypto from "crypto";
import fs from "fs";
import path from "path";

const OUT = "/work/out";
const BASE = process.env.NEXTSEEK_BASE_URL || "https://nextseek-dev.mit.edu";
const U = process.env.SEEK_USER || "demo";
const P = process.env.SEEK_PASS;
const PROBE_FILE = "/work/step3_probe.txt";
const EXPECTED_PROBE = fs.readFileSync(PROBE_FILE, "utf8").trim();
const FIRST_REPORT = "STEP3-REPORT-A-7719";
const SECOND_REPORT = "STEP3-REPORT-B-8826";

if (!P) {
  console.error("BLOCKER=missing_SEEK_PASS");
  process.exit(2);
}

fs.mkdirSync(OUT, { recursive: true });

function sha256(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function assertTruthy(value, label) {
  if (!value) {
    console.error(`ASSERT_FAIL=${label}`);
    process.exit(1);
  }
  console.log(`ASSERT_OK=${label}`);
}

async function fetchJson(page, url) {
  return page.evaluate(async (u) => {
    const r = await fetch(u, { credentials: "include" });
    const text = await r.text();
    return { ok: r.ok, status: r.status, text, json: text ? JSON.parse(text) : null };
  }, url);
}

async function login(page) {
  await page.goto(`${BASE}/login/`, { waitUntil: "domcontentloaded", timeout: 40000 });
  await page.fill("#username", U);
  await page.fill("#password", P);
  await Promise.all([
    page.waitForNavigation({ timeout: 40000 }).catch(() => {}),
    page.click("button[type=submit]"),
  ]);
  await page.waitForTimeout(1500);
  const loggedIn = !(await page.title()).includes("Sign In");
  console.log("LOGGED_IN=", loggedIn);
  assertTruthy(loggedIn, "login");
}

async function sendTurn(page, query, tag, maxMs) {
  const ta = page.locator('textarea[placeholder*="Ask NExtSEEK"]').first();
  await ta.click();
  await ta.fill(query);
  const before = (await page.evaluate(() => document.body.innerText)).length;
  await ta.press("Enter");
  let last = -1;
  let stable = 0;
  const step = 3000;
  const iters = Math.ceil(maxMs / step);
  for (let i = 0; i < iters; i++) {
    await page.waitForTimeout(step);
    const len = (await page.evaluate(() => document.body.innerText)).length;
    if (len === last) stable += 1;
    else {
      stable = 0;
      last = len;
    }
    const enabled = await ta.isEnabled().catch(() => true);
    if (stable >= 3 && enabled && len > before) break;
  }
  const body = await page.evaluate(() => document.body.innerText);
  const tail = body.slice(-700);
  const errish = /\b(404|not found|something went wrong|query submission failed|upload failed)\b/i.test(tail);
  console.log(`${tag}_ERRISH=${errish} TAIL=${JSON.stringify(tail)}`);
  assertTruthy(!errish, `${tag}_no_errorish_tail`);
  return body;
}

async function downloadButtonBytes(page, index, label) {
  const buttons = page.locator('[data-testid="artifact-download"]');
  const count = await buttons.count();
  assertTruthy(count > index, `${label}_download_button_present`);
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 30000 }),
    buttons.nth(index).click(),
  ]);
  const filePath = await download.path();
  const buf = fs.readFileSync(filePath);
  console.log(`${label}_DOWNLOAD_FILENAME=${download.suggestedFilename()}`);
  console.log(`${label}_DOWNLOAD_BYTES=${buf.length}`);
  console.log(`${label}_DOWNLOAD_SHA256=${sha256(buf)}`);
  return buf;
}

async function sessionTurns(page, sessionId) {
  const res = await fetchJson(page, `/nextseek_api/assistant/sessions/${sessionId}/?include=turns`);
  assertTruthy(res.ok, `session_turns_http_${res.status}`);
  return res.json;
}

let sessionId = null;
const taskIds = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, acceptDownloads: true });
const page = await ctx.newPage();

page.on("response", async (r) => {
  try {
    const url = r.url();
    if (url.includes("/nextseek_api/cc-assistant/query/async/") && r.ok()) {
      const j = await r.json();
      if (j.session_id) sessionId = j.session_id;
      if (j.task_id) taskIds.push(j.task_id);
      console.log("QUERY_ACCEPTED_SESSION_ID=", j.session_id);
      console.log("QUERY_ACCEPTED_TASK_ID=", j.task_id);
    }
  } catch (_) {}
});

console.log("=== STEP3 LIVE GAP CLOSURE START ===");
console.log("BASE=", BASE);
console.log("SEEK_USER=", U);
console.log("SEEK_PASS=[REDACTED]");

await login(page);
await page.goto(`${BASE}/seek/assistant/`, { waitUntil: "domcontentloaded", timeout: 40000 });
await page.waitForTimeout(2500);
await page.getByRole("button", { name: "New chat", exact: true }).first().click().catch(() => {});
await page.waitForTimeout(1500);

const uploadInput = page.locator('[data-testid="upload-control"] input[type="file"]');
await uploadInput.setInputFiles(PROBE_FILE);
await page.getByRole("button", { name: "Upload", exact: true }).click();
console.log("UPLOAD_CLICKED=true");
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(2000);
  const text = await page.locator('[data-testid="upload-control"]').textContent();
  const buttonVisible = await page.getByRole("button", { name: "Upload", exact: true }).isVisible().catch(() => false);
  if (!text?.includes("Upload failed") && !buttonVisible) {
    console.log("UPLOAD_UI_DONE=true iter=", i);
    break;
  }
  assertTruthy(!text?.includes("Upload failed"), "upload_ui_no_failure");
}

const query1 = [
  "Use bash only.",
  `Read /data/input/step3_probe.txt and confirm the exact line is ${EXPECTED_PROBE}.`,
  `Write exactly ${FIRST_REPORT} followed by a newline to /data/scratch/report.md.`,
  "Write exactly STEP3-RAW-A followed by a newline to /data/scratch/raw/raw-note.txt.",
  `Reply with Output: ${FIRST_REPORT}`,
].join(" ");
const body1 = await sendTurn(page, query1, "CC1", 220000);
assertTruthy(body1.includes(FIRST_REPORT), "first_report_visible");
assertTruthy(sessionId, "session_id_captured");

let turns = await sessionTurns(page, sessionId);
const ccTraceCount1 = turns.turns?.flatMap((t) => t.cc_traces || []).length ?? 0;
console.log("CC_TRACES_COUNT_AFTER_TURN1=", ccTraceCount1);
assertTruthy(ccTraceCount1 > 0, "cc_traces_after_turn1");

await page.getByText("Search Details").last().click();
await page.waitForTimeout(500);
assertTruthy((await page.locator('[data-testid="cc-activity-panel"]').count()) > 0, "activity_panel_visible");

const firstBytes = await downloadButtonBytes(page, 0, "FIRST_REPORT");
assertTruthy(firstBytes.toString("utf8").includes(FIRST_REPORT), "first_download_expected_bytes");

const query2 = [
  "Use bash only.",
  `Write exactly ${SECOND_REPORT} followed by a newline to /data/scratch/report.md.`,
  "Write exactly STEP3-RAW-B followed by a newline to /data/scratch/raw/raw-note.txt.",
  `Reply with Output: ${SECOND_REPORT}`,
].join(" ");
const body2 = await sendTurn(page, query2, "CC2", 220000);
assertTruthy(body2.includes(SECOND_REPORT), "second_report_visible");
console.log("TURN2_NO404=", !/\b404\b/i.test(body2));

await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(6000);
turns = await sessionTurns(page, sessionId);
const ccTraces = turns.turns?.flatMap((t) => t.cc_traces || []) ?? [];
console.log("CC_TRACES_COUNT_AFTER_RELOAD=", ccTraces.length);
console.log("CC_TRACES_JSON_EXCERPT=", JSON.stringify({
  session_id: sessionId,
  turns: turns.turns?.map((t) => ({
    turn_id: t.id,
    assistant_reply: t.assistant_reply?.slice?.(0, 120),
    cc_traces: t.cc_traces,
  })),
}, null, 2));
assertTruthy(ccTraces.length >= 2, "cc_traces_after_reload_two_turns");

await page.getByText("Search Details").last().click();
await page.waitForTimeout(500);
assertTruthy((await page.locator('[data-testid="cc-activity-panel"]').count()) > 0, "activity_panel_visible_after_reload");

const secondBytes = await downloadButtonBytes(page, 1, "SECOND_REPORT");
assertTruthy(secondBytes.toString("utf8").includes(SECOND_REPORT), "second_download_expected_bytes");
assertTruthy(sha256(firstBytes) !== sha256(secondBytes), "same_basename_download_hashes_differ");

let transcriptRecovered = false;
for (const taskId of taskIds) {
  for (const trace of ccTraces) {
    const ccSid = trace?.cc_session_id;
    if (!ccSid) continue;
    const rec = await page.evaluate(async ({ sessionId: sid, taskId: tid, ccSid: cid }) => {
    const r = await fetch(`/nextseek_api/cc-assistant/transcript/${sid}/${tid}?cc_session_id=${encodeURIComponent(cid)}`, {
      credentials: "include",
    });
    const text = await r.text();
    return { ok: r.ok, status: r.status, len: text.length, hasReportA: text.includes("STEP3-REPORT-A-7719"), hasReportB: text.includes("STEP3-REPORT-B-8826") };
    }, { sessionId, taskId, ccSid });
    console.log(`TRANSCRIPT_RECOVER_${taskId}_${ccSid}=`, JSON.stringify(rec));
    if (rec.ok && (rec.hasReportA || rec.hasReportB)) {
      transcriptRecovered = true;
      console.log("TRANSCRIPT_RECOVER_OK=true");
      break;
    }
  }
  if (transcriptRecovered) break;
}
assertTruthy(transcriptRecovered, "transcript_recover_http_200_contains_turn");

await browser.close();
console.log("STEP3_LIVE_GAP_CLOSURE_DONE");
