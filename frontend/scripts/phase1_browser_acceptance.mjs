import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP_URL = process.env.PHASE1_FRONTEND_URL || "http://127.0.0.1:5173";
const EDGE_PATH = process.env.PHASE1_EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ACCOUNTS = ["SM-01", "SM-02", "SM-03", "SM-04", "BM-01", "BM-02", "PL-01", "LD-01"];
const REPORT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "reports");
const WATCH = process.argv.includes("--watch");

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function pauseForObservation(label) {
  if (WATCH) {
    console.log(`  ${label}`);
    await sleep(2000);
  }
}

async function waitForChatResponse(page, action) {
  const assistantRows = page.locator(".message-row.assistant-row");
  const before = await assistantRows.count();
  const responsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST" && response.url().endsWith("/chat"),
    { timeout: 90000 },
  );
  await action();
  const response = await responsePromise;
  if (!response.ok()) throw new Error(`chat request returned HTTP ${response.status()}`);
  await assistantRows.nth(before).waitFor({ state: "visible", timeout: 90000 });
  await page.locator(".typing").waitFor({ state: "hidden", timeout: 90000 }).catch(() => {});
  await pauseForObservation("Answer or clarification visible");
  return response;
}

async function holdForEnter() {
  if (!WATCH) return;
  console.log("Watch mode: browser remains open. Press Enter in this terminal to finish.");
  process.stdin.resume();
  await new Promise((resolve) => process.stdin.once("data", resolve));
}

function credentials() {
  const result = {};
  for (const account of ACCOUNTS) {
    const value = process.env[`PHASE1_PASSWORD_${account.replace("-", "_")}`];
    if (value) result[account] = value;
  }
  return result;
}

async function main() {
  try {
    await fs.access(EDGE_PATH);
  } catch {
    throw new Error(`Microsoft Edge executable not found: ${EDGE_PATH}`);
  }
  const browser = await chromium.launch({
    headless: false,
    executablePath: EDGE_PATH,
    slowMo: 1200,
    args: ["--start-maximized"],
  });
  const smokeContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const smoke = await smokeContext.newPage();
  await smoke.goto(APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await smoke.getByRole("heading", { name: /sign in to inventory ai/i }).waitFor({ timeout: 15000 });
  await fs.mkdir(REPORT_DIR, { recursive: true });
  await smoke.screenshot({ path: path.join(REPORT_DIR, "phase1_browser_smoke.png"), fullPage: true });
  console.log(`VISIBLE_BROWSER_READY url=${smoke.url()}`);

  const passwords = credentials();
  const missing = ACCOUNTS.filter((account) => !passwords[account]);
  if (missing.length) {
    console.log(`BLOCKED_MISSING_CREDENTIALS accounts=${missing.join(",")}`);
    console.log("Smoke navigation passed; browser remains visible for inspection.");
    await holdForEnter();
    await smokeContext.close();
    await browser.close();
    return 2;
  }
  await smokeContext.close();

  const results = [];
  for (const account of ACCOUNTS) {
    if (WATCH) {
      console.log(`Testing ${account}`);
      if (results.length) await sleep(3000);
    }
    const context = await browser.newContext();
    const page = await context.newPage({ viewport: { width: 1600, height: 900 } });
    try {
      await page.goto(APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.getByLabel("Username").fill(account);
      await page.getByLabel("Password").fill(passwords[account]);
      await page.getByRole("button", { name: /sign in/i }).click();
      await page.getByText(new RegExp(`Account:\\s*${account}`, "i")).waitFor({ timeout: 30000 });
      const body = await page.locator("body").innerText();
      if (!body.includes(account)) throw new Error("account identity not visible after login");
      if (WATCH) console.log("  Sending authorized inventory question");
      const examples = page.locator("button").filter({ hasText: /inventory|brand|store|stockout|risk|reorder|sales/i });
      if (await examples.count()) {
        await waitForChatResponse(page, () => examples.first().click());
        let clarificationChoices = page.locator(".clarification-option");
        for (let step = 0; step < 2 && await clarificationChoices.count(); step += 1) {
          if (WATCH) console.log("  Completing clarification choice");
          await waitForChatResponse(page, () => clarificationChoices.first().click());
          clarificationChoices = page.locator(".clarification-option");
        }
        const suggestion = page.locator(".suggestion-button").first();
        if (await suggestion.count()) {
          if (WATCH) console.log("  Checking Continue Exploring");
          await waitForChatResponse(page, () => suggestion.click());
        }
      }
      if (WATCH) console.log("  Verifying logout");
      const logout = page.getByRole("button", { name: /^logout$/i });
      await logout.click();
      await page.getByRole("heading", { name: /sign in to inventory ai/i }).waitFor({ timeout: 15000 });
      results.push({ account, status: "PASS" });
      if (WATCH) console.log("  Account completed");
    } catch (error) {
      results.push({ account, status: "FAIL", error: String(error.message || error) });
    } finally {
      await context.close();
    }
  }
  console.log(JSON.stringify({ status: results.every((item) => item.status === "PASS") ? "PASS" : "FAIL", results }));
  await holdForEnter();
  await browser.close();
  return results.every((item) => item.status === "PASS") ? 0 : 1;
}

main().catch((error) => {
  console.error(`BLOCKED ${error.message}`);
  process.exitCode = 2;
});
