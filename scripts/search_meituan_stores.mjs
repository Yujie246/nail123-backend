import { chromium } from "playwright";
import { access, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8").trim();
  return text ? JSON.parse(text) : {};
}

function uniqueQueries(payload) {
  const rawQueries = Array.isArray(payload.searchQueries)
    ? payload.searchQueries
    : [payload.searchQuery || payload.keyword || "美甲"];
  const result = [];
  for (const query of rawQueries) {
    const normalized = normalizeText(query || "美甲");
    if (normalized && !result.includes(normalized)) result.push(normalized);
  }
  return result.length ? result : ["美甲"];
}

function searchUrls(query, cityId) {
  const encoded = encodeURIComponent(query || "美甲");
  return [
    {
      source: "dianping_keyword",
      url: `https://www.dianping.com/search/keyword/${cityId || "3"}/0_${encoded}`,
    },
  ];
}

async function canRead(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function isTruthy(value) {
  return /^(1|true|yes|on)$/i.test(String(value || "").trim());
}

function numberFrom(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function log(message) {
  if (isTruthy(process.env.MEITUAN_PLAYWRIGHT_QUIET)) return;
  console.error(`[meituan-search] ${message}`);
}

function isLoginUrl(url) {
  return /passport|account|login|pclogin/i.test(url);
}

function isVerifyUrl(url) {
  return /verify|spider|optimus/i.test(url);
}

function looksLikeLoginText(text) {
  return /扫码登录|手机号登录|账号登录|验证码登录|请先登录|登录后查看|注册\/登录|美团账号|点评账号/i.test(text || "");
}

function looksLikeVerifyText(text) {
  return /验证中心|安全验证|环境异常|访问验证|spiderindefence|requestCode/i.test(text || "");
}

function storeKey(store) {
  const shopMatch = String(store.externalUrl || "").match(/\/shop\/([^/?#]+)/i);
  if (shopMatch) return `shop:${shopMatch[1].toLowerCase()}`;
  return `${normalizeText(store.name).toLowerCase()}|${normalizeText(store.address).toLowerCase()}`;
}

async function saveStorageState(context, sessionPath) {
  try {
    await mkdir(dirname(sessionPath), { recursive: true });
    await context.storageState({ path: sessionPath });
  } catch (error) {
    log(`storage state save failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function pageSummary(page) {
  return page.evaluate(() => ({
    title: document.title,
    text: (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 500),
    shopLinkCount: document.querySelectorAll(
      "a[href*='/shop/'], a[href*='dianping.com/shop'], a[href*='meituan.com/shop'], a[href*='poi']",
    ).length,
    cardCount: document.querySelectorAll(
      "li, article, [data-shopid], [class*='shop'], [class*='poi'], [class*='result'], [class*='card']",
    ).length,
    linkCount: document.querySelectorAll("a[href]").length,
  }));
}

function pageHasUsableResults(summary) {
  if (!summary) return false;
  if (summary.shopLinkCount > 0) return true;
  return summary.cardCount > 5 && /美甲|美睫|nail|商户|人均|点评|地址/.test(summary.text || "");
}

async function waitForUsableSearchState(page, context, sessionPath, options) {
  const startedAt = Date.now();
  const loginDeadline = startedAt + options.loginWaitMs;
  const settleDeadline = startedAt + options.settleWaitMs;
  let prompted = false;
  let lastSummary = null;

  while (Date.now() < settleDeadline || (options.loginWaitMs > 0 && Date.now() < loginDeadline)) {
    lastSummary = await pageSummary(page);
    if (pageHasUsableResults(lastSummary)) {
      await saveStorageState(context, sessionPath);
      return { state: "usable", summary: lastSummary };
    }

    const verifyLike = isVerifyUrl(page.url()) || looksLikeVerifyText(lastSummary.text);
    if (verifyLike) {
      if (options.loginWaitMs <= 0) return { state: "verification_required", summary: lastSummary };
      if (!prompted) {
        log("detected Meituan/Dianping verification gate; finish verification in the visible browser");
        prompted = true;
      }
      if (Date.now() >= loginDeadline) return { state: "verification_timeout", summary: lastSummary };
      await page.waitForTimeout(2000);
      continue;
    }

    const loginLike = isLoginUrl(page.url()) || looksLikeLoginText(lastSummary.text);
    if (loginLike) {
      if (options.loginWaitMs <= 0) return { state: "login_required", summary: lastSummary };
      if (!prompted) {
        log("detected login gate; finish login in the visible browser and the script will continue automatically");
        prompted = true;
      }
      if (Date.now() >= loginDeadline) return { state: "login_timeout", summary: lastSummary };
      await page.waitForTimeout(2000);
      continue;
    }

    if (lastSummary.linkCount > 0 && Date.now() >= settleDeadline) {
      await saveStorageState(context, sessionPath);
      return { state: "open", summary: lastSummary };
    }

    await page.waitForTimeout(900);
  }

  return { state: "unknown", summary: lastSummary };
}

async function gentleScroll(page, rounds) {
  for (let index = 0; index < rounds; index += 1) {
    await page.mouse.wheel(0, 650 + index * 80);
    await page.waitForTimeout(650);
  }
}

const payload = await readStdinJson();
const queries = uniqueQueries(payload);
const cityId = normalizeText(payload.dianpingCityId || process.env.DIANPING_CITY_ID || "3");
const sessionPath = resolve(payload.sessionPath || process.env.MEITUAN_SESSION_FILE || "backend/.meituan_session.json");
const headless = process.env.MEITUAN_PLAYWRIGHT_HEADLESS !== "0";
const interactive = isTruthy(payload.interactive) || isTruthy(process.env.MEITUAN_PLAYWRIGHT_INTERACTIVE) || !headless;
const timeoutMs = Math.max(6000, numberFrom(process.env.MEITUAN_PLAYWRIGHT_PAGE_TIMEOUT, 9000));
const settleWaitMs = Math.max(2500, numberFrom(process.env.MEITUAN_PLAYWRIGHT_SETTLE_TIMEOUT, Math.min(timeoutMs, 9000)));
const loginWaitMs = interactive
  ? Math.max(15000, numberFrom(process.env.MEITUAN_PLAYWRIGHT_LOGIN_WAIT_SECONDS, 180) * 1000)
  : Math.max(0, numberFrom(process.env.MEITUAN_PLAYWRIGHT_LOGIN_WAIT_SECONDS, 0) * 1000);
const maxStores = Math.max(3, Number(payload.limit || process.env.MEITUAN_PLAYWRIGHT_STORE_LIMIT || "12"));
const scrollRounds = Math.max(2, numberFrom(process.env.MEITUAN_PLAYWRIGHT_SCROLL_ROUNDS, interactive ? 6 : 4));
const slowMo = Math.max(0, numberFrom(process.env.MEITUAN_PLAYWRIGHT_SLOWMO, interactive ? 250 : 0));

const contextOptions = {
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  viewport: { width: 1366, height: 900 },
  userAgent:
    process.env.MEITUAN_USER_AGENT ||
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  extraHTTPHeaders: {
    Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
  },
};
if (await canRead(sessionPath)) {
  contextOptions.storageState = sessionPath;
}

const browser = await chromium.launch({
  headless,
  slowMo,
  args: ["--disable-blink-features=AutomationControlled"],
});
const context = await browser.newContext(contextOptions);
await context.addInitScript(() => {
  Object.defineProperty(navigator, "webdriver", { get: () => undefined });
});
const page = await context.newPage();
const storesByKey = new Map();
const errors = [];
const pages = [];

try {
  for (const query of queries) {
    for (const target of searchUrls(query, cityId)) {
      try {
        await page.goto(target.url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
        const ready = await waitForUsableSearchState(page, context, sessionPath, {
          loginWaitMs,
          settleWaitMs,
        });
        pages.push({
          query,
          source: target.source,
          requestedUrl: target.url,
          finalUrl: page.url(),
          readiness: ready.state,
          ...(ready.summary || {}),
        });
        if (
          ready.state === "login_required" ||
          ready.state === "login_timeout" ||
          ready.state === "verification_required" ||
          ready.state === "verification_timeout"
        ) {
          errors.push(`${ready.state}:${page.url()}`);
          continue;
        }

        await gentleScroll(page, scrollRounds);
        const nextStores = await page.evaluate((activeQuery) => {
          const nailRe = /美甲|美睫|nail|指甲|甲艺|甲片|美手|手足|穿戴甲|延长甲/i;
          const badNameRe = /^(登录|注册|首页|更多|筛选|排序|全部|商户|图片|查看|评价|人均|收藏|分享|搜索|写点评)$/;

          function textOf(node) {
            return (node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();
          }
          function attr(node, name) {
            return node?.getAttribute?.(name) || "";
          }
          function absoluteUrl(url) {
            if (!url || /^javascript:/i.test(url)) return "";
            try {
              return new URL(url, location.href).href;
            } catch {
              return "";
            }
          }
          function cleanName(value) {
            return String(value || "")
              .replace(/\s+/g, " ")
              .replace(/^(商户|门店|店铺)[:：\s]*/, "")
              .replace(/[>\u203a].*$/, "")
              .trim()
              .slice(0, 80);
          }
          function firstImage(root) {
            const images = [...root.querySelectorAll("img")];
            for (const image of images) {
              const url = absoluteUrl(
                image.currentSrc ||
                  attr(image, "src") ||
                  attr(image, "data-src") ||
                  attr(image, "data-original") ||
                  attr(image, "lazy-src") ||
                  "",
              );
              if (url && !/^data:image/i.test(url)) return url;
            }
            return "";
          }
          function priceFrom(text) {
            const match = text.match(/(?:人均|¥|￥)\s*([0-9]{1,5}(?:\.[0-9])?)/);
            return match ? match[1] : "";
          }
          function ratingFrom(text) {
            const match = text.match(/(?:评分|评价)?\s*([0-5](?:\.[0-9])?)\s*分/);
            return match ? match[1] : "";
          }
          function addressFrom(text) {
            const match =
              text.match(/(?:地址|商区)[:：]?\s*([^¥￥人均评分电话收藏分享]{3,90})/) ||
              text.match(/([^\s]{2,12}(?:路|街|巷|弄|号|广场|中心|大厦|商场|城|店)[^¥￥人均评分电话]{0,70})/);
            return match ? match[1].replace(/\s+/g, " ").trim() : "";
          }
          function bestTextCandidate(values) {
            for (const raw of values) {
              const value = cleanName(raw);
              if (!value || value.length < 2 || value.length > 80) continue;
              if (badNameRe.test(value)) continue;
              if (/美团|大众点评|登录|注册|下载APP|全部分类|合作招商/.test(value)) continue;
              return value;
            }
            return "";
          }
          function nameFrom(root, link, fallbackText) {
            const explicit =
              root.querySelector("h4") ||
              root.querySelector("h3") ||
              root.querySelector("[class*='shop-name']") ||
              root.querySelector("[class*='shopName']") ||
              root.querySelector("[class*='title']") ||
              root.querySelector("[class*='tit']") ||
              root.querySelector("[class*='name']");
            const image = root.querySelector("img");
            const beforeMeta = fallbackText.split(/地址|商区|人均|评分|评价|¥|￥|团购|套餐/)[0];
            return bestTextCandidate([
              attr(link, "title"),
              attr(link, "aria-label"),
              textOf(link),
              textOf(explicit),
              attr(image, "alt"),
              beforeMeta,
            ]);
          }
          function closestCard(link) {
            return (
              link.closest("li") ||
              link.closest("article") ||
              link.closest("[data-shopid]") ||
              link.closest("[class*='shop']") ||
              link.closest("[class*='poi']") ||
              link.closest("[class*='result']") ||
              link.closest("[class*='card']") ||
              link.parentElement ||
              link
            );
          }

          const links = [
            ...document.querySelectorAll(
              "a[href*='/shop/'], a[href*='dianping.com/shop'], a[href*='meituan.com/shop'], a[href*='poi']",
            ),
          ];
          const result = [];
          for (const link of links) {
            const href = absoluteUrl(attr(link, "href"));
            if (!href || !/(\/shop\/|dianping\.com\/shop|meituan\.com\/shop|\/poi)/i.test(href)) continue;
            const root = closestCard(link);
            const text = textOf(root);
            if (text.length < 4) continue;
            const name = nameFrom(root, link, text);
            if (!name) continue;
            if (!nailRe.test(`${name} ${text} ${activeQuery}`)) continue;
            result.push({
              name,
              address: addressFrom(text) || "地址以美团/点评门店页为准",
              rating: ratingFrom(text),
              price: priceFrom(text),
              photo: firstImage(root),
              externalUrl: href,
              text,
              searchQuery: activeQuery,
            });
          }
          return result;
        }, query);
        for (const store of nextStores) {
          const key = storeKey(store);
          if (!storesByKey.has(key)) storesByKey.set(key, store);
        }
        if (nextStores.length > 0) await saveStorageState(context, sessionPath);
      } catch (error) {
        errors.push(`${query}:${error instanceof Error ? error.message : String(error)}`);
      }
      if (storesByKey.size >= maxStores) break;
    }
    if (storesByKey.size >= maxStores) break;
  }
} finally {
  await browser.close();
}

process.stdout.write(
  JSON.stringify({
    success: true,
    searchQuery: queries[0],
    searchQueries: queries,
    stores: [...storesByKey.values()].slice(0, maxStores),
    errors,
    pages,
    sessionPath,
    interactive,
  }),
);
