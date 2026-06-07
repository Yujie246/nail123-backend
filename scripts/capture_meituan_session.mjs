import { chromium } from "playwright";
import { access, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const sessionPath = resolve(process.env.MEITUAN_SESSION_FILE || "backend/.meituan_session.json");
const loginUrl = process.env.MEITUAN_LOGIN_URL || "https://passport.meituan.com/account/unitivelogin";
const dianpingUrl =
  process.env.DIANPING_LOGIN_CHECK_URL ||
  "https://www.dianping.com/search/keyword/3/0_%E7%BE%8E%E7%94%B2";
const loginWaitSeconds = Math.max(30, Number(process.env.MEITUAN_PLAYWRIGHT_LOGIN_WAIT_SECONDS || "180"));

async function canRead(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function isLoginUrl(url) {
  return /passport|account|login|pclogin/i.test(url);
}

function looksLikeLoginText(text) {
  return /扫码登录|手机号登录|账号登录|验证码登录|请先登录|登录后查看|注册\/登录|美团账号|点评账号/i.test(text || "");
}

async function pageSummary(page) {
  return page.evaluate(() => ({
    title: document.title,
    text: (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 500),
    shopLinkCount: document.querySelectorAll(
      "a[href*='/shop/'], a[href*='dianping.com/shop'], a[href*='meituan.com/shop'], a[href*='poi']",
    ).length,
    linkCount: document.querySelectorAll("a[href]").length,
  }));
}

async function saveStorage(context) {
  await mkdir(dirname(sessionPath), { recursive: true });
  await context.storageState({ path: sessionPath });
  console.log(`Saved Meituan/Dianping login state to ${sessionPath}`);
}

async function waitForSearchOrLogin(page, context) {
  console.log("\n浏览器已打开，正在自动检测美团/点评登录状态。");
  console.log("如果页面要求登录，请在浏览器里扫码或输入验证码；检测到门店结果后会自动保存，不用回终端按 Enter。");

  const deadline = Date.now() + loginWaitSeconds * 1000;
  let prompted = false;
  while (Date.now() < deadline) {
    const summary = await pageSummary(page);
    if (summary.shopLinkCount > 0 || /美甲|美睫|nail|商户|人均|地址/.test(summary.text)) {
      await saveStorage(context);
      return true;
    }

    const loginLike = isLoginUrl(page.url()) || looksLikeLoginText(summary.text);
    if (loginLike && !prompted) {
      console.log("检测到登录提示，等待你在浏览器里完成登录...");
      prompted = true;
    }

    await page.waitForTimeout(2000);
  }
  return false;
}

const contextOptions = {
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  viewport: { width: 1366, height: 900 },
  userAgent:
    process.env.MEITUAN_USER_AGENT ||
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
};
if (await canRead(sessionPath)) {
  contextOptions.storageState = sessionPath;
}

const browser = await chromium.launch({
  headless: false,
  slowMo: 250,
  args: ["--disable-blink-features=AutomationControlled"],
});
const context = await browser.newContext(contextOptions);
await context.addInitScript(() => {
  Object.defineProperty(navigator, "webdriver", { get: () => undefined });
});
const page = await context.newPage();

try {
  await page.goto(dianpingUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
} catch (error) {
  console.log(`Dianping navigation changed before load finished: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`);
}

let ok = await waitForSearchOrLogin(page, context);
if (!ok) {
  console.log("点评搜索页未检测到可用结果，尝试打开美团登录页...");
  await page.goto(loginUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  ok = await waitForSearchOrLogin(page, context);
}

await browser.close();

if (!ok) {
  console.error(`登录检测超时：${loginWaitSeconds} 秒内未检测到可用门店结果`);
  process.exit(1);
}
