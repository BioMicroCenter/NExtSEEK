// Two-turn CC --resume verification THROUGH THE UI (Step 1b).
import { chromium } from "playwright";

const OUT = "/work/out";
const BASE = process.env.NEXTSEEK_BASE_URL || "https://nextseek-dev.mit.edu";
const U = process.env.SEEK_USER;
const P = process.env.SEEK_PASS;
const TURN1 = "Use bash to echo this codeword exactly and confirm it ran: BANANA-42";
const TURN2 = "Use bash to echo the same codeword from my first message, but change the number 42 to 84.";

const b = await chromium.launch();
const ctx = await b.newContext({ ignoreHTTPSErrors: true });
const p = await ctx.newPage();
const sh = async (t) => p.screenshot({ path: `${OUT}/${t}.png`, fullPage: true }).catch(() => {});

await p.goto(BASE + "/login/", { waitUntil: "domcontentloaded", timeout: 40000 });
await p.fill("#username", U);
await p.fill("#password", P);
await Promise.all([p.waitForNavigation({ timeout: 40000 }).catch(() => {}), p.click("button[type=submit]")]);
await p.waitForTimeout(1500);
console.log("LOGGED_IN=", !(await p.title()).includes("Sign In"));

await p.goto(BASE + "/seek/assistant/", { waitUntil: "domcontentloaded", timeout: 40000 });
await p.waitForTimeout(2500);
await p.getByRole("button", { name: "New chat", exact: true }).first().click().catch(() => {});
await p.waitForTimeout(1500);
await sh("T0-newchat");

const ta = () => p.locator('textarea[placeholder*="Ask NExtSEEK"]').first();

async function sendTurn(query, tag, maxMs) {
  const t = ta();
  await t.click();
  await t.fill(query);
  await sh(`${tag}-typed`);
  const before = (await p.evaluate(() => document.body.innerText)).length;
  await t.press("Enter");
  let last = -1;
  let stable = 0;
  const step = 3000;
  const iters = Math.ceil(maxMs / step);
  for (let i = 0; i < iters; i++) {
    await p.waitForTimeout(step);
    const len = (await p.evaluate(() => document.body.innerText)).length;
    if (len === last) stable += 1;
    else {
      stable = 0;
      last = len;
    }
    const enabled = await ta().isEnabled().catch(() => true);
    if (stable >= 3 && enabled && len > before) break;
  }
  await sh(`${tag}-response`);
  const body = await p.evaluate(() => document.body.innerText);
  const errish = /\b(404|error|failed|not found|something went wrong)\b/i.test(body.slice(before));
  console.log(`${tag}_GREW_BY=${body.length - before} ERRISH=${errish} TAIL=${JSON.stringify(body.slice(-600))}`);
  return body;
}

await sendTurn(TURN1, "T1", 200000);
const body2 = await sendTurn(TURN2, "T2", 200000);
const norm = (s) => s.toUpperCase().replace(/[-\s]/g, "");
const recalled = norm(body2).includes("BANANA84");
console.log("CODEWORD_RECALLED_84=", recalled);
console.log(recalled ? "UI MULTI-TURN RESUME: VERIFIED" : "UI MULTI-TURN RESUME: NOT CONFIRMED");
await b.close();
if (!recalled) process.exit(1);
console.log("UI_DONE");
