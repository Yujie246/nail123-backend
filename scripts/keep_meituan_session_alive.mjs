import { chromium } from "playwright";
import { access, mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const sessionPath = resolve(process.env.MEITUAN_SESSION_FILE || "backend/.meituan_session.json");
const keepAliveUrl =
  process.env.MEITUAN_KEEPALIVE_URL ||
  "https://www.dianping.com/search/keyword/3/0_%E7%BE%8E%E7%94%B2";
const dianpingKeepAliveUrl =
  process.env.DIANPING_KEEPALIVE_URL ||
  "https://www.dianping.com/search/keyword/3/0_%E7%BE%8E%E7%94%B2";
const intervalMinutes = Math.max(3, Number(process.env.MEITUAN_KEEPALIVE_MINUTES || "20"));
const headless = process.env.MEITUAN_KEEPALIVE_HEADLESS !== "0";
const once = process.argv.includes("--once");

async function ensureSessionFile() {
  try {
    await access(sessionPath);
  } catch {
    throw new Error(`Missing ${sessionPath}. Run npm run meituan:login first.`);
  }
}

async function refreshSession() {
  await ensureSessionFile();
  const browser = await chromium.launch({ headless });
  const context = await browser.newContext({ storageState: sessionPath });
  const page = await context.newPage();
  try {
    await page.goto(keepAliveUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(1800);
    await page.goto(dianpingKeepAliveUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(1800);
    const finalUrl = page.url();
    if (/passport|account|login/i.test(finalUrl)) {
      console.log(`[meituan:keepalive] Login expired, final URL: ${finalUrl}`);
      console.log("[meituan:keepalive] Run npm run meituan:login and finish login again.");
      return false;
    }
    const storage = await context.storageState();
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(sessionPath, JSON.stringify(storage, null, 2), "utf8");
    console.log(`[meituan:keepalive] Refreshed session at ${new Date().toISOString()}`);
    return true;
  } finally {
    await browser.close();
  }
}

while (true) {
  const ok = await refreshSession().catch((error) => {
    console.error(`[meituan:keepalive] ${error instanceof Error ? error.message : String(error)}`);
    return false;
  });
  if (once) break;
  const waitMs = (ok ? intervalMinutes : 3) * 60 * 1000;
  await new Promise((resolveDelay) => setTimeout(resolveDelay, waitMs));
}
