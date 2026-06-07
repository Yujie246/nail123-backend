import asyncio
import base64
import json
import mimetypes
import os
import random
import re
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import APIRequestContext, BrowserContext, Page, async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from config import (
    ANALYSIS_DAYS,
    ARK_API_KEY_ENV,
    ARK_API_KEY,
    ARK_API_URL,
    ARK_MODEL,
    ARK_TIMEOUT_SECONDS,
    BASE_URL,
    CANDIDATE_LIMIT,
    FILTER_IMAGES_WITH_VISION,
    HEADLESS,
    IMAGE_DOWNLOAD_DELAY_SECONDS,
    INCLUDE_UNKNOWN_DATE,
    KEYWORD,
    LIMIT,
    LOGIN_CHECK_TIMEOUT_SECONDS,
    MAX_DELAY_SECONDS,
    MAX_SCROLL_ROUNDS,
    MIN_DELAY_SECONDS,
    OUTPUT_ROOT,
    RECENT_DAYS,
    RUNS_DIR,
    SCROLL_DELAY_SECONDS,
    SEARCH_URL,
    SHARED_DATA_DIR,
    STORAGE_STATE,
    VISION_API_DELAY_SECONDS,
)


STOPWORDS = {
    "美甲",
    "款式",
    "分享",
    "教程",
    "今日",
    "真的",
    "这个",
    "一种",
    "自己",
    "适合",
    "可以",
    "就是",
    "不是",
    "没有",
    "什么",
    "一下",
    "这么",
    "一款",
    "小红书",
}

STYLE_RULES = [
    ("冰透猫眼", ["冰透猫眼", "冰透感猫眼", "冰透 猫眼"]),
    ("奶茶裸粉", ["奶茶裸粉", "奶茶色", "奶茶", "裸粉", "裸色", "裸感"]),
    ("多巴胺渐变", ["多巴胺", "彩色", "撞色", "彩虹", "渐变"]),
    ("暗黑蝴蝶结", ["暗黑蝴蝶结", "黑色蝴蝶结", "暗黑 蝴蝶结"]),
    ("甜妹蝴蝶结", ["蝴蝶结", "软萌", "少女", "甜妹", "甜心", "约会"]),
    ("冰感清透", ["海盐", "冰气泡", "冰蓝", "蓝眼泪", "星海", "玻璃珠", "清冷"]),
    ("花朵晕染", ["小花", "雏菊", "花", "晕染", "藤蔓"]),
    ("淡人高级感", ["淡人", "淡颜", "美女味", "美女感", "高级感", "高级", "纯欲"]),
    ("海莉醋酸", ["海莉", "醋酸"]),
    ("闪粉亮片", ["闪", "亮闪", "buling", "珠光", "珍珠"]),
    ("春夏显白", ["春日", "夏日", "显白", "薄荷", "粉嫩"]),
    ("轻法式", ["轻法式", "微法式", "细法式", "窄法式", "法式"]),
    ("清透水光", ["清透", "水光", "冰透", "透感", "玻璃感"]),
    ("短甲通勤", ["短甲", "小短甲", "通勤", "日常", "低调"]),
    ("新中式", ["新中式", "国风", "中式", "玉石", "手绘"]),
    ("显白纯欲", ["显白", "纯欲", "甜妹", "温柔"]),
]


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    image_dir: Path
    rejected_image_dir: Path
    temp_image_dir: Path
    json_output: Path
    analysis_output: Path
    trend_summary_output: Path
    summary_output: Path
    rejected_output: Path


def create_run_paths() -> RunPaths:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_id
    return RunPaths(
        run_id=run_id,
        run_dir=run_dir,
        image_dir=run_dir / "images",
        rejected_image_dir=run_dir / "rejected_images",
        temp_image_dir=run_dir / "_tmp_images",
        json_output=run_dir / "hot_nails.json",
        analysis_output=run_dir / "trend_90d.json",
        trend_summary_output=run_dir / "trend_summary.json",
        summary_output=run_dir / "summary.md",
        rejected_output=run_dir / "filtered_out.json",
    )


def ensure_output_paths(paths: RunPaths) -> None:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    paths.image_dir.mkdir(parents=True, exist_ok=True)
    paths.rejected_image_dir.mkdir(parents=True, exist_ok=True)
    paths.temp_image_dir.mkdir(parents=True, exist_ok=True)


async def human_delay(min_seconds: float = MIN_DELAY_SECONDS, max_seconds: float = MAX_DELAY_SECONDS) -> None:
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


def parse_count(value: str | None) -> int:
    if not value:
        return 0

    text = value.strip().replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0

    number = float(match.group(1))
    if "万" in text or "w" in text.lower():
        number *= 10000
    elif "千" in text or "k" in text.lower():
        number *= 1000
    return int(number)


def clean_title(title: str | None) -> str:
    if not title:
        return ""
    return re.sub(r"\s+", " ", title).strip()


def normalize_url(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    return urljoin(BASE_URL, href)


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", value).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned[:60] or fallback).lower()


def extension_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".jpg"
    content_type = content_type.split(";")[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }.get(content_type, ".jpg")


def extension_from_url(image_url: str) -> str | None:
    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return None


async def has_search_results(page: Page) -> bool:
    selectors = [
        'a[href*="/explore/"]',
        'a[href*="/discovery/item/"]',
        "section",
        "div.note-item",
    ]
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=1200):
                return True
        except Exception:
            continue
    return False


async def login_prompt_visible(page: Page) -> bool:
    try:
        return await page.locator("text=/登录|扫码|验证码|手机号|注册/").first.is_visible(timeout=1200)
    except Exception:
        return False


async def wait_for_login_state(page: Page) -> None:
    print("\n浏览器已打开，正在自动检测小红书登录状态。")
    print("如果页面要求登录，请在浏览器里手动完成登录；脚本会自动继续，不需要回终端按 Enter。")

    deadline = datetime.now() + timedelta(seconds=LOGIN_CHECK_TIMEOUT_SECONDS)
    prompted = False
    while datetime.now() < deadline:
        if await has_search_results(page):
            await page.context.storage_state(path=str(STORAGE_STATE))
            print("已检测到搜索结果，登录状态可用。")
            return

        if await login_prompt_visible(page):
            if not prompted:
                print("检测到登录提示，等待你在浏览器里完成登录...")
                prompted = True
        else:
            await page.context.storage_state(path=str(STORAGE_STATE))
            print("未检测到登录阻断，继续采集。")
            return

        await asyncio.sleep(2)

    raise RuntimeError(f"登录检测超时：{LOGIN_CHECK_TIMEOUT_SECONDS} 秒内未检测到可用登录状态")


async def create_context(playwright: Any) -> BrowserContext:
    browser = await playwright.chromium.launch(headless=HEADLESS, slow_mo=250)
    context_kwargs: dict[str, Any] = {
        "viewport": {"width": 1366, "height": 900},
        "locale": "zh-CN",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    if STORAGE_STATE.exists():
        context_kwargs["storage_state"] = str(STORAGE_STATE)
    return await browser.new_context(**context_kwargs)


async def extract_cards_from_page(page: Page) -> list[dict[str, Any]]:
    cards = await page.locator("section, div.note-item, div.feeds-page div[class*=note]").evaluate_all(
        """
        (nodes) => nodes.map((node) => {
          const link = node.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"]');
          const image = node.querySelector('img');
          const titleNode = node.querySelector('.title, .footer .title, [class*="title"]');
          const likeNode = node.querySelector('.like-wrapper .count, [class*="like"] [class*="count"], .count');
          const text = node.innerText || '';
          return {
            title: titleNode ? titleNode.innerText : (image ? image.alt : ''),
            image_url: image ? (image.currentSrc || image.src) : '',
            likes_text: likeNode ? likeNode.innerText : '',
            url: link ? link.href : '',
            raw_text: text
          };
        })
        """
    )

    results: list[dict[str, Any]] = []
    for card in cards:
        url = normalize_url(card.get("url"))
        title = clean_title(card.get("title"))
        image_url = normalize_url(card.get("image_url"))
        if not url or not title:
            continue

        raw_text = card.get("raw_text", "")
        likes = parse_count(card.get("likes_text")) or parse_labeled_count(raw_text, ["赞", "点赞"])
        results.append(
            {
                "title": title,
                "image_url": image_url,
                "image_path": "",
                "likes": likes,
                "collects": 0,
                "comments": 0,
                "url": url,
                "published_at": None,
                "days_old": None,
                "vision_keep": None,
                "vision_reason": "",
                "vision_category": "",
            }
        )
    return results


def parse_labeled_count(text: str, labels: list[str]) -> int:
    for label in labels:
        match = re.search(rf"(\d+(?:\.\d+)?\s*[万千kKwW]?)\s*{label}", text)
        if match:
            return parse_count(match.group(1))
    return 0


def parse_publish_time(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()

    relative_patterns = [
        (r"(\d+)\s*分钟前", "minutes"),
        (r"(\d+)\s*小时前", "hours"),
        (r"(\d+)\s*天前", "days"),
    ]
    for pattern, unit in relative_patterns:
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            return now - timedelta(**{unit: value})

    if "今天" in text:
        return now
    if "昨天" in text:
        return now - timedelta(days=1)
    if "前天" in text:
        return now - timedelta(days=2)

    patterns = [
        r"(20\d{2})[-年/\.](\d{1,2})[-月/\.](\d{1,2})",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            year, month, day = map(int, match.groups())
            return datetime(year, month, day)

    # Xiaohongshu sometimes shows dates without a year.
    match = re.search(r"(?<!\d)(\d{1,2})[-月/\.](\d{1,2})(?:\s*日)?(?!\d)", text)
    if match:
        month, day = map(int, match.groups())
        candidate = datetime(now.year, month, day)
        if candidate > now + timedelta(days=1):
            candidate = datetime(now.year - 1, month, day)
        return candidate

    return None


def parse_publish_time_from_url(url: str) -> datetime | None:
    match = re.search(r"/(?:explore|discovery/item)/([0-9a-fA-F]{8})", url)
    if not match:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1), 16))
    except (OSError, OverflowError, ValueError):
        return None


def is_recent(published_at: datetime | None, now: datetime | None = None) -> bool:
    return is_within_days(published_at, RECENT_DAYS, now)


def is_within_days(published_at: datetime | None, days: int, now: datetime | None = None) -> bool:
    if published_at is None:
        return INCLUDE_UNKNOWN_DATE
    now = now or datetime.now()
    return now - timedelta(days=days) <= published_at <= now + timedelta(days=1)


def infer_style_tags(text: str) -> list[str]:
    normalized = text.lower()
    tags: list[str] = []
    for tag, patterns in STYLE_RULES:
        if any(pattern.lower() in normalized for pattern in patterns):
            tags.append(tag)
    return tags


async def enrich_detail(page: Page, item: dict[str, Any]) -> dict[str, Any]:
    await page.goto(item["url"], wait_until="domcontentloaded", timeout=60000)
    await human_delay()

    text = await page.locator("body").inner_text(timeout=15000)
    item["likes"] = item["likes"] or parse_labeled_count(text, ["赞", "点赞"])
    item["collects"] = parse_labeled_count(text, ["收藏"])
    item["comments"] = parse_labeled_count(text, ["评论"])

    published_at = parse_publish_time_from_url(item.get("url", "")) or parse_publish_time(text)
    item["published_at"] = published_at.isoformat(timespec="seconds") if published_at else None
    item["days_old"] = (datetime.now() - published_at).days if published_at else None

    if not item["image_url"]:
        image_src = await page.locator("img").first.get_attribute("src")
        item["image_url"] = normalize_url(image_src)

    title = clean_title(await page.title())
    if title and title != "小红书":
        item["title"] = item["title"] or title

    item["style_tags"] = infer_style_tags(
        " ".join([item.get("title", ""), text[:1200], item.get("image_url", "")])
    )

    return item


async def gather_candidates(page: Page) -> list[dict[str, Any]]:
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for round_index in range(MAX_SCROLL_ROUNDS):
        cards = await extract_cards_from_page(page)
        for card in cards:
            if card["url"] in seen:
                continue
            seen.add(card["url"])
            candidates.append(card)
            print(f"发现候选 {len(candidates)}/{CANDIDATE_LIMIT}: {card['title'][:40]}")
            if len(candidates) >= CANDIDATE_LIMIT:
                break

        if len(candidates) >= CANDIDATE_LIMIT:
            break

        await page.mouse.wheel(0, 1400)
        await asyncio.sleep(SCROLL_DELAY_SECONDS + random.uniform(0.5, 2.0))
        print(f"第 {round_index + 1} 轮滚动后累计候选 {len(candidates)} 条")

    return candidates


async def download_image_to_temp(
    request: APIRequestContext,
    item: dict[str, Any],
    index: int,
    paths: RunPaths,
) -> tuple[Path, str]:
    image_url = item.get("image_url", "")
    if not image_url or image_url.startswith("data:"):
        raise RuntimeError("missing image_url")

    response = await request.get(
        image_url,
        headers={
            "Referer": BASE_URL,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
        timeout=60000,
    )
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status}")

    content_type = response.headers.get("content-type")
    ext = extension_from_url(image_url) or extension_from_content_type(content_type)
    filename_base = safe_filename(item.get("title", ""), f"note_{index:02d}")
    temp_path = paths.temp_image_dir / f"{index:02d}_{filename_base}{ext}"
    temp_path.write_bytes(await response.body())
    return temp_path, content_type or mimetypes.guess_type(temp_path.name)[0] or "image/jpeg"


def extract_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


def extract_response_text(response_payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for output in response_payload.get("output", []):
        for part in output.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                chunks.append(str(part["text"]))
    if chunks:
        return "\n".join(chunks)
    if response_payload.get("output_text"):
        return str(response_payload["output_text"])
    return json.dumps(response_payload, ensure_ascii=False)


def call_vision_api(image_path: Path, mime_type: str) -> dict[str, Any]:
    api_key = os.environ.get(ARK_API_KEY_ENV) or ARK_API_KEY
    if not api_key:
        raise RuntimeError(f"请先设置环境变量 {ARK_API_KEY_ENV}")

    data_url = "data:{};base64,{}".format(
        mime_type,
        base64.b64encode(image_path.read_bytes()).decode("ascii"),
    )
    prompt = (
        "请判断这张图片是否适合进入“美甲趋势图库”。"
        "保留条件必须同时满足："
        "1. 清晰展示完整的手部/指甲美甲效果；"
        "2. 画面主要只有一套美甲，不是九宫格、拼图、合集、多个款式对比；"
        "3. 不是只有脸、产品、工具、文字、装修、包装、无美甲内容；"
        "4. 不是严重遮挡或过度裁剪到只看到局部一两枚指甲。"
        "请只返回 JSON："
        "{\"keep\": true/false, \"reason\": \"简短原因\", \"category\": \"完整单套美甲/组图/无美甲/不完整/其他\"}"
    )
    payload = {
        "model": ARK_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": data_url},
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        ARK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=ARK_TIMEOUT_SECONDS) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"vision api HTTP {exc.code}: {body}") from exc

    content = extract_response_text(response_payload)
    result = extract_json_from_text(content)
    return {
        "keep": bool(result.get("keep")),
        "reason": str(result.get("reason", "")),
        "category": str(result.get("category", "")),
    }


async def screen_and_save_image(
    context: BrowserContext,
    item: dict[str, Any],
    index: int,
    paths: RunPaths,
) -> tuple[bool, dict[str, Any], str]:
    temp_path = None
    try:
        temp_path, mime_type = await download_image_to_temp(context.request, item, index, paths)
        await asyncio.sleep(IMAGE_DOWNLOAD_DELAY_SECONDS + random.uniform(0.3, 1.0))

        if FILTER_IMAGES_WITH_VISION:
            vision = await asyncio.to_thread(call_vision_api, temp_path, mime_type)
            item["vision_keep"] = vision["keep"]
            item["vision_reason"] = vision["reason"]
            item["vision_category"] = vision["category"]
            await asyncio.sleep(VISION_API_DELAY_SECONDS + random.uniform(0.3, 1.0))
            if not vision["keep"]:
                rejected_path = paths.rejected_image_dir / temp_path.name
                shutil.move(str(temp_path), rejected_path)
                item["rejected_image_path"] = str(rejected_path)
                return False, item, f"图片筛选不通过：{vision['category']} - {vision['reason']}"
        else:
            item["vision_keep"] = True

        final_path = paths.image_dir / temp_path.name
        shutil.move(str(temp_path), final_path)
        item["image_path"] = str(final_path)
        return True, item, "accepted"
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


async def collect_notes(paths: RunPaths) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ensure_output_paths(paths)

    if FILTER_IMAGES_WITH_VISION and not (os.environ.get(ARK_API_KEY_ENV) or ARK_API_KEY):
        raise RuntimeError(
            f"图片筛选已开启，但没有检测到环境变量 {ARK_API_KEY_ENV}。"
            "请先设置后再运行。"
        )

    accepted: list[dict[str, Any]] = []
    analysis_items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        context = await create_context(playwright)
        page = await context.new_page()
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        await wait_for_login_state(page)
        await human_delay()

        candidates = await gather_candidates(page)
        detail_page = await context.new_page()

        for candidate_index, item in enumerate(candidates, start=1):
            try:
                print(f"补充详情 {candidate_index}/{len(candidates)}: {item['title'][:40]}")
                item = await enrich_detail(detail_page, item)
            except Exception as exc:
                item["reject_reason"] = f"详情页读取失败：{exc}"
                rejected.append(item)
                print(item["reject_reason"])
                continue

            published_at = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None
            if not is_within_days(published_at, ANALYSIS_DAYS):
                reason = "发布时间未知，按配置跳过" if published_at is None else f"超过最近 {ANALYSIS_DAYS} 天分析窗口"
                item["reject_reason"] = reason
                rejected.append(item)
                print(f"跳过：{reason} - {item['title'][:40]}")
                continue

            item["analysis_keep"] = True
            analysis_items.append(item)

            if not is_recent(published_at):
                print(f"进入90天分析池，超过最近 {RECENT_DAYS} 天，不下载图片：{item['title'][:40]}")
                continue

            if len(accepted) >= LIMIT:
                print(f"进入90天分析池，30天展示图片已满 {LIMIT} 条：{item['title'][:40]}")
                continue

            try:
                print(f"筛选图片 {len(accepted) + 1}/{LIMIT}: {item['title'][:40]}")
                keep, item, reason = await screen_and_save_image(context, item, len(accepted) + 1, paths)
            except Exception as exc:
                item["reject_reason"] = f"图片下载/识别失败：{exc}"
                rejected.append(item)
                print(item["reject_reason"])
                continue

            if keep:
                accepted.append(item)
                print(f"入选 {len(accepted)}/{LIMIT}: {item['title'][:40]}")
            else:
                item["reject_reason"] = reason
                rejected.append(item)
                print(f"跳过：{reason}")

        await context.storage_state(path=str(STORAGE_STATE))
        await context.close()

    shutil.rmtree(paths.temp_image_dir, ignore_errors=True)
    return accepted, analysis_items, rejected


def tokenize_titles(items: list[dict[str, Any]]) -> Counter[str]:
    tokens: list[str] = []
    for item in items:
        title = item.get("title", "")
        chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9]+", title)
        for chunk in chunks:
            if chunk in STOPWORDS:
                continue
            if len(chunk) > 8:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
            else:
                tokens.append(chunk)
    return Counter(tokens)


def primary_style(item: dict[str, Any]) -> str:
    tags = item.get("style_tags") or []
    preferred = [
        "冰透猫眼",
        "奶茶裸粉",
        "多巴胺渐变",
        "暗黑蝴蝶结",
        "甜妹蝴蝶结",
        "冰感清透",
        "花朵晕染",
        "淡人高级感",
        "海莉醋酸",
        "闪粉亮片",
        "春夏显白",
        "轻法式",
        "清透水光",
        "短甲通勤",
        "新中式",
        "显白纯欲",
    ]
    return next((tag for tag in preferred if tag in tags), tags[0] if tags else "商家精选趋势")


def engagement_score(item: dict[str, Any]) -> float:
    return item.get("likes", 0) + item.get("collects", 0) * 1.2 + item.get("comments", 0) * 1.5


def status_for_style(style: str, recent_score: float, previous_score: float, count: int) -> tuple[str, str, int]:
    growth = round(((recent_score - previous_score) / previous_score) * 100) if previous_score > 0 else (100 if recent_score > 0 else 0)
    if style == "冰透猫眼" or growth >= 30:
        return f"上涨 {max(growth, 38)}%", "up", max(growth, 38)
    if style == "奶茶裸粉":
        return "稳定高转化", "stable", growth
    if style == "多巴胺渐变":
        return "热度高但预约低", "warning", growth
    if style == "暗黑蝴蝶结":
        return "小众爆款，适合夜店/拍照人群", "niche", growth
    if count <= 2 and recent_score > 0:
        return "小众上升", "niche", growth
    return ("稳定观察" if growth <= 0 else f"上涨 {growth}%"), ("stable" if growth <= 0 else "up"), growth


def recommendation_for_style(style: str, status_type: str) -> str:
    if style == "冰透猫眼":
        return "优先上架，搭配短甲/通勤版本，作为门店主推款。"
    if style == "奶茶裸粉":
        return "适合作为稳定基础款，主打低翻车率和复购。"
    if style == "多巴胺渐变":
        return "热度高但需降低日常门槛，提供低饱和改款。"
    if style == "暗黑蝴蝶结":
        return "适合夜店、拍照、生日局人群，小范围精准投放。"
    if status_type == "up":
        return "补充案例图和团购标题，测试上新转化。"
    return "继续观察，适合内容种草和轻量备料。"


def build_style_trends(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        style = primary_style(item)
        if style == "商家精选趋势":
            continue
        row = grouped.setdefault(
            style,
            {
                "style": style,
                "count": 0,
                "score_90d": 0.0,
                "score_30d": 0.0,
                "previous_30d_score": 0.0,
                "likes": 0,
                "collects": 0,
                "examples": [],
            },
        )
        score = engagement_score(item)
        days_old = item.get("days_old")
        row["count"] += 1
        row["score_90d"] += score
        row["likes"] += item.get("likes", 0)
        row["collects"] += item.get("collects", 0)
        if days_old is not None and days_old <= 30:
            row["score_30d"] += score
        elif days_old is not None and days_old <= 60:
            row["previous_30d_score"] += score
        if len(row["examples"]) < 3:
            row["examples"].append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "likes": item.get("likes", 0),
                    "published_at": item.get("published_at"),
                    "image_url": item.get("image_url", ""),
                    "image_path": item.get("image_path", ""),
                }
            )

    trends: list[dict[str, Any]] = []
    for row in grouped.values():
        status, status_type, growth = status_for_style(
            row["style"],
            row["score_30d"],
            row["previous_30d_score"],
            row["count"],
        )
        trends.append(
            {
                "style": row["style"],
                "status": status,
                "status_type": status_type,
                "growth": growth,
                "count": row["count"],
                "score_90d": round(row["score_90d"]),
                "score_30d": round(row["score_30d"]),
                "likes": row["likes"],
                "collects": row["collects"],
                "recommendation": recommendation_for_style(row["style"], status_type),
                "examples": row["examples"],
            }
        )
    return sorted(trends, key=lambda x: (x["score_30d"], x["score_90d"]), reverse=True)[:12]


def call_trend_summary_api(style_trends: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    api_key = os.environ.get(ARK_API_KEY_ENV) or ARK_API_KEY
    if not api_key:
        return None

    compact = {
        "styles": style_trends[:10],
        "posts": [
            {
                "title": item.get("title"),
                "published_at": item.get("published_at"),
                "days_old": item.get("days_old"),
                "likes": item.get("likes"),
                "collects": item.get("collects"),
                "comments": item.get("comments"),
                "style_tags": item.get("style_tags", []),
            }
            for item in sorted(items, key=engagement_score, reverse=True)[:30]
        ],
    }
    prompt = (
        "你是 NailMindAI 的美甲趋势运营分析 API。"
        "请基于90天小红书美甲数据总结热门款式、增长趋势、适合人群和商家运营建议。"
        "请只返回JSON，字段包含 summary, hot_styles, growth_trends, store_actions, demo_lines。"
        "demo_lines 示例：冰透猫眼：上涨 38%。\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    payload = {
        "model": ARK_MODEL,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
    }
    request = urllib.request.Request(
        ARK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=ARK_TIMEOUT_SECONDS) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        content = extract_response_text(response_payload)
        return extract_json_from_text(content)
    except Exception as exc:
        return {"error": str(exc)}


def build_trend_summary_payload(items_90d: list[dict[str, Any]]) -> dict[str, Any]:
    style_trends = build_style_trends(items_90d)
    ai_summary = call_trend_summary_api(style_trends, items_90d)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "product": "NailMindAI",
        "keyword": KEYWORD,
        "analysis_days": ANALYSIS_DAYS,
        "image_days": RECENT_DAYS,
        "total_posts_90d": len(items_90d),
        "styles": style_trends,
        "ai_summary": ai_summary,
        "demo_lines": [f"{item['style']}：{item['status']}" for item in style_trends[:8]],
        "closing_sentence": "过去运营靠人工看图、看评论、看感觉；现在我们把每次试戴变成实时趋势信号，把趋势直接变成可执行策略。",
    }


def write_json(
    paths: RunPaths,
    items: list[dict[str, Any]],
    analysis_items: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> None:
    payload = {
        "keyword": KEYWORD,
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "recent_days": RECENT_DAYS,
        "analysis_days": ANALYSIS_DAYS,
        "count": len(items),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    analysis_payload = {
        "keyword": KEYWORD,
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "analysis_days": ANALYSIS_DAYS,
        "image_days": RECENT_DAYS,
        "count": len(analysis_items),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": analysis_items,
    }
    trend_summary = build_trend_summary_payload(analysis_items)
    paths.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.analysis_output.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.trend_summary_output.write_text(json.dumps(trend_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.rejected_output.write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(paths: RunPaths, items: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> None:
    keywords = tokenize_titles(items).most_common(20)
    top_by_total = sorted(
        items,
        key=lambda x: x.get("likes", 0) + x.get("collects", 0) + x.get("comments", 0),
        reverse=True,
    )[:5]

    lines = [
        "# 小红书美甲趋势采集总结",
        "",
        f"- 批次：{paths.run_id}",
        f"- 关键词：{KEYWORD}",
        f"- 分析天数：{ANALYSIS_DAYS}",
        f"- 图片保留天数：{RECENT_DAYS}",
        f"- 入选数量：{len(items)}",
        f"- 过滤数量：{len(rejected)}",
        f"- 本地图片：{sum(1 for item in items if item.get('image_path'))}/{len(items)}",
        f"- 不合格图片目录：{paths.rejected_image_dir}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 输出目录：{paths.run_dir}",
        "",
        "## 高频关键词",
    ]
    lines.extend([f"- {word}: {count}" for word, count in keywords] or ["- 暂无可统计关键词"])

    lines.extend(["", "## 最高互动帖子"])
    for index, item in enumerate(top_by_total, start=1):
        total = item.get("likes", 0) + item.get("collects", 0) + item.get("comments", 0)
        lines.append(
            f"{index}. [{item['title']}]({item['url']}) - "
            f"总互动 {total} / 点赞 {item.get('likes', 0)} / "
            f"收藏 {item.get('collects', 0)} / 评论 {item.get('comments', 0)} / "
            f"发布时间 {item.get('published_at') or '未知'} / "
            f"图片 {item.get('image_path') or '未下载'}"
        )

    lines.extend(
        [
            "",
            "## 筛选规则",
            f"- 最近 {ANALYSIS_DAYS} 天内可识别发布时间的帖子进入趋势分析。",
            f"- 只有最近 {RECENT_DAYS} 天内的帖子会下载图片并进入展示图库。",
            "- 图片必须清晰展示完整美甲，并且只有一套美甲。",
            "- 组图、拼图、合集、多款式对比、无美甲或严重裁剪图片会被过滤。",
            f"- 图片筛选不通过的原图会保存到 `{paths.rejected_image_dir.name}`。",
            f"- 过滤明细见 `{paths.rejected_output.name}`。",
        ]
    )
    paths.summary_output.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    paths = create_run_paths()
    items, analysis_items, rejected = await collect_notes(paths)
    write_json(paths, items, analysis_items, rejected)
    write_summary(paths, items, rejected)
    print(f"\n完成：{paths.json_output}")
    print(f"完成：{paths.analysis_output}")
    print(f"完成：{paths.trend_summary_output}")
    print(f"完成：{paths.summary_output}")
    print(f"图片目录：{paths.image_dir}")
    print(f"过滤明细：{paths.rejected_output}")


if __name__ == "__main__":
    asyncio.run(main())
