from __future__ import annotations

import json
import html as html_lib
import re
import subprocess
import uuid as uuid_lib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .doubao_client import first_env, load_env
except ImportError:
    from doubao_client import first_env, load_env


PROJECT_ROOT = Path(__file__).resolve().parent.parent

STYLE_KEYWORDS = (
    "猫眼",
    "法式",
    "渐变",
    "晕染",
    "腮红",
    "冰透",
    "透色",
    "裸色",
    "奶茶",
    "豆沙",
    "珍珠",
    "贝壳",
    "碎钻",
    "钻饰",
    "蝴蝶结",
    "格纹",
    "豹纹",
    "玫瑰",
    "花朵",
    "小花",
    "手绘",
    "磁吸",
    "亮片",
    "银闪",
    "金箔",
    "短甲",
    "长甲",
    "方圆",
    "杏仁",
    "显白",
    "通勤",
    "高级感",
    "甜酷",
    "轻法式",
    "低饱和",
)

MEITUAN_COOKIE_KEYS = ("MEITUAN_COOKIE", "MEITUAN_SESSION_COOKIE")
MEITUAN_SESSION_FILE_KEYS = ("MEITUAN_SESSION_FILE", "MEITUAN_STORAGE_STATE")
MEITUAN_SNAPSHOT_KEYS = ("MEITUAN_STORE_SNAPSHOT", "MEITUAN_STORE_MATCH_FILE")
AMAP_API_KEY_KEYS = ("AMAP_WEB_SERVICE_KEY", "AMAP_API_KEY", "GAODE_API_KEY")
DEFAULT_MEITUAN_CITY_ID = "50"
MEITUAN_PC_SEARCH_LIMIT = 20
LAST_PLAYWRIGHT_SEARCH_STATUS: dict[str, Any] = {}
HEFEI_SHUSHAN_STORE_POOL_PATH = PROJECT_ROOT / "backend" / "data" / "hefei_shushan_nail_stores.json"
UI_PROMPT_PLAN_BUNDLE_PATH = PROJECT_ROOT / "backend" / "data" / "nail_tryon_prompt_plans" / "ui_prompt_plan_bundle.json"
UI_PROMPT_PLAN_BUNDLE_CACHE: dict[str, Any] | None = None
STYLE_MATCH_ALIASES = {
    "轻法式": ["轻法式", "法式"],
    "奶茶": ["奶茶", "裸色", "裸粉"],
    "奶茶裸粉": ["奶茶", "裸色", "裸粉"],
    "裸粉": ["裸粉", "裸色", "奶茶"],
    "裸色": ["裸色", "裸粉", "奶茶"],
    "冰透": ["冰透", "清透", "水光"],
    "清透": ["清透", "水光", "冰透"],
    "水光": ["水光", "清透", "冰透"],
    "碎钻": ["碎钻", "钻饰", "珍珠"],
    "钻饰": ["钻饰", "碎钻", "珍珠"],
    "甜妹": ["甜妹", "甜美", "花朵", "蝴蝶结"],
    "甜酷": ["甜酷", "猫眼", "碎钻"],
    "高级感": ["高级感", "轻奢", "裸色", "猫眼"],
    "日式": ["日式", "简约", "通勤"],
}


def get_meituan_status() -> dict[str, Any]:
    load_env()
    cookie = read_meituan_cookie()
    amap_key = first_env(*AMAP_API_KEY_KEYS)
    session_file = resolve_meituan_session_path()
    snapshot_file = resolve_optional_path(first_env(*MEITUAN_SNAPSHOT_KEYS))
    hefei_pool_count = count_hefei_shushan_store_pool()
    return {
        "configured": bool(cookie),
        "hasCookie": bool(cookie),
        "hasNearbyMapKey": bool(amap_key),
        "hefeiShushanStorePoolEnabled": hefei_shushan_store_pool_enabled(),
        "hefeiShushanStorePoolCount": hefei_pool_count,
        "sessionFileConfigured": bool(session_file),
        "sessionFileExists": bool(session_file and session_file.exists()),
        "snapshotFileConfigured": bool(snapshot_file),
        "snapshotFileExists": bool(snapshot_file and snapshot_file.exists()),
        "liveSearchEnabled": live_search_enabled(),
        "mode": (
            "hefei_shushan_store_pool"
            if hefei_shushan_store_pool_enabled()
            else "signed_cookie_live_search"
            if cookie and live_search_enabled()
            else "live_search"
        ),
    }


def build_meituan_store_response(payload: dict[str, Any], demand: dict[str, Any] | None) -> dict[str, Any]:
    load_env()
    keyword = str(payload.get("keyword") or "美甲").strip() or "美甲"
    terms, search_query = build_search_query(demand, keyword)
    latitude = safe_float(payload.get("latitude"))
    longitude = safe_float(payload.get("longitude"))
    radius = safe_int(payload.get("radius"), 3000)

    reset_playwright_search_status()
    hefei_pool_stores = fetch_hefei_shushan_store_matches(terms, search_query, latitude, longitude)
    live_stores = [] if hefei_pool_stores else fetch_live_store_matches(search_query, terms, latitude, longitude)
    playwright_status = get_last_playwright_search_status()
    amap_stores = [] if hefei_pool_stores or live_stores else fetch_amap_nearby_store_matches(keyword, terms, search_query, latitude, longitude, radius)
    cached_live_stores = [] if hefei_pool_stores or live_stores or amap_stores else load_recent_live_matches(terms, search_query)
    snapshot_stores = load_snapshot_matches(terms, search_query, latitude, longitude)
    stores = select_top_stores([*hefei_pool_stores, *live_stores, *amap_stores, *cached_live_stores, *snapshot_stores], terms, search_query, limit=3)
    if not stores:
        stores.append(build_search_handoff_store(search_query, terms, demand, latitude, longitude))

    primary_source = next((store.get("source") for store in stores if store.get("source") != "meituan_search_handoff"), "")
    session = get_meituan_status()
    if primary_source == "hefei_shushan_store_pool":
        message = f"已从合肥蜀山区 10 家真实门店池中，按当前美甲款式推荐 Top {len(stores)} 家。"
    elif primary_source == "meituan_playwright_search":
        message = f"已用 Playwright 登录态搜索美团/点评，并筛选出 Top {len(stores)} 门店。"
    elif primary_source == "meituan_live_search":
        message = f"已按需求关键词爬取美团搜索结果，并筛选出 Top {len(stores)} 门店。"
    elif primary_source == "amap_place_around":
        message = f"美团/点评实时解析暂未命中新门店，已按你的定位从高德实时返回附近 Top {len(stores)} 家真实美甲门店。"
    elif primary_source == "meituan_live_search_cache":
        if playwright_status.get("loginRequired"):
            message = f"点评要求重新登录，本次先展示最近抓取过的真实美团/点评 Top {len(stores)}；运行 npm run meituan:login 刷新登录态后可实时更新。"
        else:
            message = f"实时门店源本次暂未返回新结果，已临时展示最近抓取过的真实美团/点评 Top {len(stores)}；建议配置 AMAP_WEB_SERVICE_KEY 或刷新美团/点评登录态。"
    elif primary_source == "meituan_local_snapshot":
        message = f"美团实时搜索暂未解析到门店，已按本地美团案例快照筛选 Top {len(stores)}。"
    else:
        if playwright_status.get("loginRequired"):
            message = "点评要求重新登录，已返回搜索入口；运行 npm run meituan:login 刷新登录态后可实时解析门店。"
        else:
            message = "美团实时搜索暂未解析到门店，已返回搜索入口；配置本地登录态后可提高命中率。"

    session["playwrightSearch"] = playwright_status

    return {
        "success": True,
        "source": primary_source or "meituan_search_handoff",
        "stores": stores,
        "count": len(stores),
        "message": message,
        "searchQuery": search_query,
        "matchedTerms": terms,
        "meituanSession": session,
        "handoffUrl": build_meituan_search_url(search_query),
    }


def build_hefei_shushan_style_store_response(style_id: str, style_name: str = "", limit: int = 3) -> dict[str, Any]:
    load_env()
    normalized_style_id = str(style_id or "").strip()
    normalized_style_name = str(style_name or "").strip()
    target_style = resolve_ui_style_summary(normalized_style_id, normalized_style_name)
    display_name = target_style.get("name") or normalized_style_name or normalized_style_id or "当前款式"
    search_query = f"合肥蜀山区 {display_name} 美甲"
    terms = infer_terms_from_ui_style(target_style, display_name)

    stores = fetch_hefei_shushan_store_matches(terms, search_query, None, None)
    for store in stores:
        recommended_ids = [str(value) for value in store.get("recommendedStyleIds") or []]
        if normalized_style_id and normalized_style_id in recommended_ids:
            store["matchScore"] = float(store.get("matchScore") or 0) + 160
            store["matchReason"] = f"该店 mock 推荐款式中包含「{display_name}」，可直接打开点评核对并预约。"
            store["matchedTags"] = unique_terms([display_name, *terms[:4]])
        elif target_style.get("id") and str(target_style["id"]) in recommended_ids:
            store["matchScore"] = float(store.get("matchScore") or 0) + 160
            store["matchReason"] = f"该店 mock 推荐款式中包含「{display_name}」，可直接打开点评核对并预约。"
            store["matchedTags"] = unique_terms([display_name, *terms[:4]])
        store["targetStyle"] = target_style

    selected = select_top_stores(stores, terms, search_query, limit=max(1, min(int(limit or 3), 10)))
    return {
        "success": True,
        "source": "hefei_shushan_fixed_style_mock",
        "stores": selected,
        "count": len(selected),
        "style": target_style,
        "searchQuery": search_query,
        "matchedTerms": terms,
        "message": f"已按「{display_name}」从合肥蜀山区 10 家真实门店池中推荐 Top {len(selected)} 家，可直接去点评核对并预约。",
    }


def build_search_query(demand: dict[str, Any] | None, fallback_keyword: str) -> tuple[list[str], str]:
    source_text = fallback_keyword
    raw_tags: list[Any] = []
    location = ""
    if demand:
        raw_tags = demand.get("styleTags") if isinstance(demand.get("styleTags"), list) else []
        source_text = " ".join(
            str(part)
            for part in (
                fallback_keyword,
                demand.get("demandText") or "",
                demand.get("fitInfo") or "",
                demand.get("btiArchetype") or "",
            )
            if part
        )
        location = str(demand.get("location") or "").strip()

    terms: list[str] = []
    for tag in raw_tags:
        add_term(terms, str(tag))
    for keyword in STYLE_KEYWORDS:
        if keyword in source_text:
            add_term(terms, keyword)
    if not terms and fallback_keyword:
        add_term(terms, fallback_keyword)

    base_terms = ["美甲", *terms[:5]]
    query = " ".join(unique_terms(base_terms))
    if location:
        compact_location = re.sub(r"(附近|周边|[0-9.\s]+公里|[0-9.\s]+米|内)", "", location).strip()
        if compact_location and compact_location not in query:
            query = f"{compact_location} {query}"
    return unique_terms(terms[:8]), query


def load_snapshot_matches(
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    snapshot_path = resolve_optional_path(first_env(*MEITUAN_SNAPSHOT_KEYS))
    if not snapshot_path or not snapshot_path.exists():
        return []
    try:
        parsed = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []

    matches = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        normalized = normalize_snapshot_store(item, index, terms, search_query, latitude, longitude)
        if normalized:
            matches.append(normalized)

    matches.sort(key=lambda store: (store.get("matchScore") or 0, safe_float(store.get("rating")) or 0), reverse=True)
    return matches[:8]


def load_recent_live_matches(terms: list[str], search_query: str) -> list[dict[str, Any]]:
    cache_path = PROJECT_ROOT / "backend" / "mock_db" / "store_searches.json"
    if not cache_path.exists():
        return []
    try:
        parsed = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, dict):
        return []

    matches: list[dict[str, Any]] = []
    for record in parsed.values():
        if not isinstance(record, dict):
            continue
        stores = record.get("stores")
        if not isinstance(stores, list):
            continue
        for item in stores:
            if not isinstance(item, dict):
                continue
            store = dict(item)
            name = str(store.get("name") or "").strip()
            source = str(store.get("source") or "").strip()
            haystack = flatten_text(store, 1200)
            if source in {"meituan_search_handoff", "meituan_signed_search_handoff"}:
                continue
            if name.startswith(("美团搜索相似款", "美团登录搜索", "美团公开搜索", "高德地图搜索")):
                continue
            if not name or is_excluded_store(name, haystack) or not is_nail_store(name, haystack):
                continue
            matched_terms = [term for term in terms if term and term in haystack]
            if terms and not matched_terms:
                matched_terms = [str(tag) for tag in store.get("matchedTags", []) if str(tag).strip() in terms]
            store["source"] = "meituan_live_search_cache"
            store["matchedTags"] = matched_terms or [str(tag) for tag in store.get("matchedTags", []) if str(tag).strip()][:3] or terms[:3]
            store["matchScore"] = (safe_float(store.get("matchScore")) or score_store(name, haystack, matched_terms, str(store.get("rating") or ""))) - 1
            store["matchReason"] = store.get("matchReason") or build_match_reason(store["matchedTags"], [])
            matches.append(store)
    return select_top_stores(matches, terms, search_query, limit=8)


def hefei_shushan_store_pool_enabled() -> bool:
    value = first_env("HEFEI_SHUSHAN_STORE_POOL_ENABLED")
    if not value:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def count_hefei_shushan_store_pool() -> int:
    if not HEFEI_SHUSHAN_STORE_POOL_PATH.exists():
        return 0
    try:
        parsed = json.loads(HEFEI_SHUSHAN_STORE_POOL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def load_ui_prompt_plan_bundle() -> dict[str, Any]:
    global UI_PROMPT_PLAN_BUNDLE_CACHE
    if UI_PROMPT_PLAN_BUNDLE_CACHE is not None:
        return UI_PROMPT_PLAN_BUNDLE_CACHE
    if not UI_PROMPT_PLAN_BUNDLE_PATH.exists():
        UI_PROMPT_PLAN_BUNDLE_CACHE = {}
        return {}
    try:
        parsed = json.loads(UI_PROMPT_PLAN_BUNDLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        UI_PROMPT_PLAN_BUNDLE_CACHE = {}
        return {}
    UI_PROMPT_PLAN_BUNDLE_CACHE = parsed if isinstance(parsed, dict) else {}
    return UI_PROMPT_PLAN_BUNDLE_CACHE


def resolve_ui_style_summary(style_id: str, style_name: str = "") -> dict[str, Any]:
    bundle = load_ui_prompt_plan_bundle()
    candidate: Any = None
    by_id = bundle.get("byId") if isinstance(bundle.get("byId"), dict) else {}
    by_name = bundle.get("byName") if isinstance(bundle.get("byName"), dict) else {}
    if style_id:
        candidate = by_id.get(style_id)
    if not candidate and style_name:
        candidate = by_name.get(style_name)
    if not isinstance(candidate, dict):
        return {
            "id": style_id,
            "name": style_name or style_id or "当前款式",
            "image": "",
            "tags": [],
        }
    return summarize_ui_style(candidate)


def summarize_ui_style(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("uiId") or item.get("id") or ""),
        "name": str(item.get("uiName") or item.get("name") or item.get("overallStyle") or "美甲款式"),
        "image": str(item.get("uiImage") or item.get("image") or ""),
        "tags": [str(tag) for tag in item.get("uiTags") or item.get("tags") or [] if str(tag).strip()][:5],
        "overallStyle": str(item.get("overallStyle") or ""),
    }


def infer_terms_from_ui_style(style: dict[str, Any], fallback_name: str = "") -> list[str]:
    terms: list[str] = []
    for tag in style.get("tags") or []:
        add_term(terms, str(tag))
    haystack = " ".join(
        str(part)
        for part in (
            style.get("name") or "",
            style.get("overallStyle") or "",
            fallback_name,
        )
        if part
    )
    for keyword in STYLE_KEYWORDS:
        if keyword in haystack:
            add_term(terms, keyword)
    return unique_terms(terms[:8])


def build_store_recommended_styles(style_ids: list[str], fallback_images: list[str]) -> list[dict[str, Any]]:
    bundle = load_ui_prompt_plan_bundle()
    by_id = bundle.get("byId") if isinstance(bundle.get("byId"), dict) else {}
    styles: list[dict[str, Any]] = []
    for index, style_id in enumerate(style_ids[:3]):
        candidate = by_id.get(style_id)
        if isinstance(candidate, dict):
            styles.append(summarize_ui_style(candidate))
        else:
            styles.append(
                {
                    "id": style_id,
                    "name": style_id,
                    "image": fallback_images[index] if index < len(fallback_images) else "",
                    "tags": [],
                }
            )
    return styles


def fetch_hefei_shushan_store_matches(
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    if not hefei_shushan_store_pool_enabled() or not HEFEI_SHUSHAN_STORE_POOL_PATH.exists():
        return []
    try:
        parsed = json.loads(HEFEI_SHUSHAN_STORE_POOL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []

    demand_terms = infer_store_pool_terms(terms, search_query)
    stores: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        store = normalize_hefei_shushan_store(item, index, demand_terms, search_query, latitude, longitude)
        if store:
            stores.append(store)
    return select_top_stores(stores, demand_terms, search_query, limit=8)


def infer_store_pool_terms(terms: list[str], search_query: str) -> list[str]:
    result = unique_terms([term for term in terms if term and term != "美甲"])
    haystack = search_query or ""
    for keyword in STYLE_KEYWORDS:
        if keyword in haystack:
            add_term(result, keyword)
    return result


def normalize_hefei_shushan_store(
    item: dict[str, Any],
    index: int,
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any] | None:
    name = str(item.get("name") or "").strip()
    address = str(item.get("address") or "").strip()
    if not name or not address:
        return None

    style_tags = [str(tag).strip() for tag in item.get("styleTags") or [] if str(tag).strip()]
    specialties = [str(value).strip() for value in item.get("specialties") or [] if str(value).strip()]
    recommended_style_ids = [str(value).strip() for value in item.get("recommendedStyleIds") or [] if str(value).strip()][:3]
    case_images = [str(url).strip() for url in item.get("caseImages") or [] if str(url).strip()][:4]
    haystack = flatten_text(item, 1600)
    matched_terms = match_store_pool_terms(terms, haystack)
    if not matched_terms and not terms:
        matched_terms = style_tags[:3]

    source_url = str(item.get("sourceUrl") or "").strip()
    booking_url = build_dianping_search_url(f"{name} 合肥 蜀山区 美甲")
    score = score_store(name, haystack, matched_terms, str(item.get("rating") or ""))
    score += max(0, 10 - index) * 1.2
    score += len(matched_terms) * 18
    if "之心城" in haystack or "天鹅湖" in haystack or "中环" in haystack:
        score += 4

    return {
        "id": str(item.get("id") or f"hefei_shushan_store_{index}_{abs(hash(name))}"),
        "name": name,
        "address": address,
        "distance": "",
        "tel": str(item.get("tel") or ""),
        "type": "hefei_shushan_nail_store",
        "rating": str(item.get("rating") or ""),
        "price": str(item.get("price") or ""),
        "businessArea": str(item.get("businessArea") or ""),
        "priceRange": str(item.get("priceRange") or ""),
        "reviewCount": str(item.get("reviewCount") or ""),
        "monthlyOrders": str(item.get("monthlyOrders") or ""),
        "openHours": str(item.get("openHours") or ""),
        "avgDuration": str(item.get("avgDuration") or ""),
        "repeatRate": str(item.get("repeatRate") or ""),
        "platformRank": str(item.get("platformRank") or ""),
        "discount": str(item.get("discount") or ""),
        "serviceBadges": [str(value).strip() for value in item.get("serviceBadges") or [] if str(value).strip()],
        "detailTags": [str(value).strip() for value in item.get("detailTags") or [] if str(value).strip()],
        "photo": str(item.get("photo") or ""),
        "location": str(item.get("location") or ""),
        "source": "hefei_shushan_store_pool",
        "sourceUrl": source_url,
        "externalUrl": source_url or booking_url,
        "bookingUrl": booking_url,
        "matchScore": score,
        "matchReason": build_hefei_shushan_match_reason(matched_terms, style_tags, specialties),
        "matchedTags": matched_terms or style_tags[:5],
        "recommendedStyleIds": recommended_style_ids,
        "recommendedStyles": build_store_recommended_styles(recommended_style_ids, case_images),
        "caseImages": case_images,
        "searchQuery": search_query,
    }


def match_store_pool_terms(terms: list[str], haystack: str) -> list[str]:
    normalized_haystack = haystack.lower()
    matched: list[str] = []
    for term in terms:
        normalized = str(term or "").strip()
        if not normalized:
            continue
        aliases = [normalized, *STYLE_MATCH_ALIASES.get(normalized, [])]
        if any(alias and alias.lower() in normalized_haystack for alias in aliases):
            add_term(matched, normalized)
    return matched


def build_hefei_shushan_match_reason(matched_terms: list[str], style_tags: list[str], specialties: list[str]) -> str:
    if matched_terms:
        return f"蜀山区门店池匹配款式元素：{'、'.join(matched_terms[:5])}。"
    if specialties:
        return specialties[0]
    if style_tags:
        return f"蜀山区真实门店，主打：{'、'.join(style_tags[:4])}。"
    return "蜀山区真实门店，可打开点评搜索继续核对案例和可约时间。"


def fetch_amap_nearby_store_matches(
    keyword: str,
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
    radius: int,
) -> list[dict[str, Any]]:
    api_key = first_env(*AMAP_API_KEY_KEYS)
    if not api_key or latitude is None or longitude is None:
        return []

    safe_radius = max(500, min(int(radius or 3000), 10000))
    params = {
        "key": api_key,
        "keywords": keyword.strip() or "美甲",
        "location": f"{longitude},{latitude}",
        "radius": str(safe_radius),
        "offset": "20",
        "page": "1",
        "extensions": "all",
    }
    url = "https://restapi.amap.com/v3/place/around?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            raw = response.read().decode("utf-8")
    except Exception:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if str(data.get("status")) != "1":
        return []

    stores: list[dict[str, Any]] = []
    for index, poi in enumerate(data.get("pois") or []):
        if not isinstance(poi, dict):
            continue
        store = normalize_amap_store(poi, index, terms, search_query, latitude, longitude)
        if store:
            stores.append(store)
    return select_top_stores(stores, terms, search_query, limit=8)


def normalize_amap_store(
    poi: dict[str, Any],
    index: int,
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any] | None:
    name = str(poi.get("name") or "").strip()
    if not name:
        return None
    haystack = flatten_text(poi, 1600)
    if is_excluded_store(name, haystack) or not is_nail_store(name, haystack):
        return None

    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    photos = poi.get("photos") if isinstance(poi.get("photos"), list) else []
    photo = ""
    if photos and isinstance(photos[0], dict):
        photo = str(photos[0].get("url") or "")

    location = str(poi.get("location") or "").strip()
    distance = str(poi.get("distance") or "").strip() or format_distance(latitude, longitude, location)
    matched_terms = [term for term in terms if term and term in haystack]
    amap_url = amap_marker_url(name, location)
    dianping_url = build_dianping_search_url(f"{name} 美甲")

    return {
        "id": str(poi.get("id") or f"amap_nearby_{index}_{abs(hash(name))}"),
        "name": name,
        "address": str(poi.get("address") or poi.get("pname") or poi.get("adname") or "地址以地图为准"),
        "distance": distance,
        "tel": str(poi.get("tel") or ""),
        "type": str(poi.get("type") or "amap_nearby_store"),
        "rating": str(biz_ext.get("rating") or ""),
        "price": str(biz_ext.get("cost") or ""),
        "photo": photo,
        "location": location,
        "source": "amap_place_around",
        "externalUrl": amap_url,
        "bookingUrl": dianping_url,
        "matchScore": score_store(name, haystack, matched_terms, str(biz_ext.get("rating") or "")) + distance_score(distance),
        "matchReason": build_nearby_match_reason(distance, matched_terms),
        "matchedTags": matched_terms or terms[:3],
        "caseImages": [photo] if photo else [],
        "searchQuery": search_query,
    }


def amap_marker_url(name: str, location: str) -> str:
    if location and "," in location:
        return (
            "https://uri.amap.com/marker?"
            + urllib.parse.urlencode(
                {
                    "position": location,
                    "name": name or "美甲门店",
                    "src": "nail-alpha",
                }
            )
        )
    return "https://uri.amap.com/search?" + urllib.parse.urlencode({"keyword": name or "美甲", "src": "nail-alpha"})


def build_nearby_match_reason(distance: str, matched_terms: list[str]) -> str:
    prefix = f"定位附近 {distance}" if distance else "定位附近"
    if matched_terms:
        return f"{prefix} 的真实地图门店，并匹配需求元素：{'、'.join(matched_terms[:5])}。"
    return f"{prefix} 的真实地图门店，可打开美团/点评继续核对案例和预约时间。"


def distance_score(distance: str) -> float:
    meters = safe_float(distance)
    if meters is None:
        return 0
    if meters <= 500:
        return 18
    if meters <= 1000:
        return 12
    if meters <= 3000:
        return 6
    return 0


def fetch_live_store_matches(
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    if not live_search_enabled():
        return []

    stores: list[dict[str, Any]] = []
    queries = live_search_queries(search_query, terms)
    if queries and playwright_search_enabled():
        try:
            stores.extend(fetch_playwright_search_stores(queries[:3], terms, latitude, longitude))
        except Exception:
            stores = []
        stores = select_top_stores(stores, terms, search_query, limit=MEITUAN_PC_SEARCH_LIMIT)
        if len(stores) >= 3:
            return select_top_stores(stores, terms, search_query, limit=3)

    if playwright_search_enabled() and is_truthy(first_env("MEITUAN_DISABLE_HTTP_FALLBACK") or ""):
        return select_top_stores(stores, terms, search_query, limit=3)

    for query in queries[:4]:
        for fetcher in (fetch_pc_search_stores, fetch_dianping_search_stores, fetch_html_search_stores):
            try:
                next_stores = fetcher(query, terms, latitude, longitude)
            except Exception:
                next_stores = []
            stores.extend(next_stores)
            stores = select_top_stores(stores, terms, search_query, limit=MEITUAN_PC_SEARCH_LIMIT)
            if len(stores) >= 3:
                break
        if len(stores) >= 3:
            break
    return select_top_stores(stores, terms, search_query, limit=3)


def live_search_queries(search_query: str, terms: list[str]) -> list[str]:
    location_prefix = location_prefix_from_query(search_query)
    simple_terms = " ".join(terms[:4]).strip()
    tight_terms = " ".join(terms[:2]).strip()
    variants = [
        search_query,
        f"{location_prefix} 美甲 {simple_terms}",
        f"{location_prefix} 美甲 {tight_terms}",
        f"美甲 {simple_terms}",
        f"{location_prefix} 美甲",
        "美甲",
    ]
    result: list[str] = []
    for variant in variants:
        normalized = re.sub(r"\s+", " ", variant).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def location_prefix_from_query(search_query: str) -> str:
    configured = first_env("MEITUAN_DEFAULT_CITY", "DEFAULT_CITY")
    if configured:
        return configured.strip()
    city_tokens = (
        "合肥蜀山",
        "合肥",
        "杭州滨江",
        "杭州",
        "上海",
        "北京",
        "广州",
        "深圳",
        "南京",
        "成都",
        "武汉",
        "西安",
        "苏州",
    )
    for token in city_tokens:
        if token in search_query:
            return token
    return "杭州"


def reset_playwright_search_status() -> None:
    global LAST_PLAYWRIGHT_SEARCH_STATUS
    LAST_PLAYWRIGHT_SEARCH_STATUS = {"attempted": False}


def set_playwright_search_status(**status: Any) -> None:
    global LAST_PLAYWRIGHT_SEARCH_STATUS
    LAST_PLAYWRIGHT_SEARCH_STATUS = status


def get_last_playwright_search_status() -> dict[str, Any]:
    return dict(LAST_PLAYWRIGHT_SEARCH_STATUS)


def summarize_playwright_result(parsed: dict[str, Any], returncode: int = 0, stderr: str = "") -> dict[str, Any]:
    errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
    pages = parsed.get("pages") if isinstance(parsed.get("pages"), list) else []
    stores = parsed.get("stores") if isinstance(parsed.get("stores"), list) else []
    readiness_values = [str(page.get("readiness") or "") for page in pages if isinstance(page, dict)]
    final_urls = [str(page.get("finalUrl") or "") for page in pages if isinstance(page, dict)]
    login_required = any(
        "login_required" in str(error) or "login_timeout" in str(error)
        for error in errors
    ) or any("login_required" in value or "login_timeout" in value for value in readiness_values)
    login_required = login_required or any(re.search(r"account|passport|login|pclogin", url, re.I) for url in final_urls)
    verification_required = any(
        "verification_required" in str(error) or "verification_timeout" in str(error)
        for error in errors
    ) or any("verification_required" in value or "verification_timeout" in value for value in readiness_values)
    verification_required = verification_required or any(re.search(r"verify|spider|optimus", url, re.I) for url in final_urls)
    return {
        "attempted": True,
        "ok": returncode == 0,
        "loginRequired": login_required,
        "verificationRequired": verification_required,
        "storeCount": len(stores),
        "errors": [str(error).splitlines()[0][:160] for error in errors[:3]],
        "readiness": [value for value in readiness_values if value][:3],
        "stderr": stderr.splitlines()[-1][:160] if stderr.strip() else "",
    }


def fetch_playwright_search_stores(
    search_query: str | list[str],
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    if not playwright_search_enabled():
        set_playwright_search_status(attempted=False, disabled=True)
        return []

    script = PROJECT_ROOT / "scripts" / "search_meituan_stores.mjs"
    if not script.exists():
        set_playwright_search_status(attempted=False, missingScript=True)
        return []
    search_queries = search_query if isinstance(search_query, list) else [search_query]
    search_queries = [re.sub(r"\s+", " ", str(query)).strip() for query in search_queries if str(query).strip()]
    if not search_queries:
        set_playwright_search_status(attempted=False, emptyQuery=True)
        return []
    payload = {
        "searchQuery": search_queries[0],
        "searchQueries": search_queries,
        "keyword": "美甲",
        "dianpingCityId": resolve_dianping_city_id(search_queries[0]),
        "sessionPath": str(resolve_meituan_session_path()),
    }
    timeout = normalize_timeout(first_env("MEITUAN_PLAYWRIGHT_TIMEOUT") or "12")
    try:
        completed = subprocess.run(
            ["node", str(script)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=max(12, min(45, timeout * max(1, len(search_queries)))),
            check=False,
        )
    except subprocess.TimeoutExpired:
        set_playwright_search_status(attempted=True, ok=False, timeout=True, loginRequired=False, storeCount=0)
        return []
    except Exception as exc:
        set_playwright_search_status(attempted=True, ok=False, error=str(exc).splitlines()[0][:160], storeCount=0)
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        set_playwright_search_status(
            attempted=True,
            ok=False,
            returncode=completed.returncode,
            loginRequired=False,
            storeCount=0,
            stderr=completed.stderr.splitlines()[-1][:160] if completed.stderr.strip() else "",
        )
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        set_playwright_search_status(
            attempted=True,
            ok=False,
            invalidJson=True,
            loginRequired=False,
            storeCount=0,
            stdout=completed.stdout[:160],
            stderr=completed.stderr.splitlines()[-1][:160] if completed.stderr.strip() else "",
        )
        return []
    if isinstance(parsed, dict):
        set_playwright_search_status(**summarize_playwright_result(parsed, completed.returncode, completed.stderr))
    raw_stores = parsed.get("stores") if isinstance(parsed, dict) else None
    if not isinstance(raw_stores, list):
        return []

    stores = []
    for index, item in enumerate(raw_stores):
        if not isinstance(item, dict):
            continue
        store = normalize_playwright_store(item, index, search_queries[0], terms, latitude, longitude)
        if store:
            stores.append(store)
    return select_top_stores(stores, terms, search_queries[0], limit=MEITUAN_PC_SEARCH_LIMIT)


def normalize_playwright_store(
    item: dict[str, Any],
    index: int,
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any] | None:
    name = str(item.get("name") or "").strip()
    search_query = str(item.get("searchQuery") or search_query).strip() or "美甲"
    haystack = flatten_text(item, 1800)
    if not name or len(name) < 2 or len(name) > 100:
        return None
    if is_excluded_store(name, haystack) or not is_nail_store(name, haystack):
        return None
    matched_terms = [term for term in terms if term and term in haystack]
    external_url = normalize_external_url(str(item.get("externalUrl") or ""), build_dianping_search_url(search_query))
    photo = normalize_external_url(str(item.get("photo") or ""), external_url or build_dianping_search_url(search_query))
    address = str(item.get("address") or "").strip() or "地址以美团/点评门店页为准"
    location = str(item.get("location") or "").strip()
    return {
        "id": f"meituan_playwright_{index}_{abs(hash(name))}",
        "name": name,
        "address": address,
        "distance": format_distance(latitude, longitude, location),
        "tel": "",
        "type": "meituan_playwright_store",
        "rating": str(item.get("rating") or ""),
        "price": str(item.get("price") or ""),
        "photo": photo,
        "location": location,
        "source": "meituan_playwright_search",
        "externalUrl": external_url or build_dianping_search_url(f"{name} 美甲"),
        "bookingUrl": external_url or build_dianping_search_url(f"{name} 美甲"),
        "matchScore": score_store(name, haystack, matched_terms, str(item.get("rating") or "")) + 3,
        "matchReason": build_match_reason(matched_terms, []),
        "matchedTags": matched_terms or terms[:3],
        "caseImages": [photo] if photo else [],
        "searchQuery": search_query,
    }


def fetch_pc_search_stores(
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    city_id = resolve_meituan_city_id(search_query)
    params = {
        "uuid": first_env("MEITUAN_UUID") or str(uuid_lib.uuid4()),
        "userid": first_env("MEITUAN_USER_ID") or "-1",
        "limit": str(MEITUAN_PC_SEARCH_LIMIT),
        "offset": "0",
        "cateId": first_env("MEITUAN_CATE_ID") or "-1",
        "q": search_query,
    }
    url = f"https://apimobile.meituan.com/group/v4/poi/pcsearch/{city_id}?" + urllib.parse.urlencode(params)
    text, final_url = request_meituan_text(url, referer=build_meituan_search_url(search_query), accept="application/json")
    parsed = json.loads(text)
    return parse_meituan_json_payload(parsed, final_url, search_query, terms, latitude, longitude)


def fetch_html_search_stores(
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    url = build_meituan_search_url(search_query)
    text, final_url = request_meituan_text(url, referer="https://www.meituan.com/")
    stores: list[dict[str, Any]] = []
    for payload in extract_json_payloads_from_html(text):
        stores.extend(parse_meituan_json_payload(payload, final_url, search_query, terms, latitude, longitude))
    if not stores:
        stores.extend(parse_meituan_anchor_cards(text, final_url, search_query, terms))
    return select_top_stores(stores, terms, search_query, limit=3)


def fetch_dianping_search_stores(
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    city_id = resolve_dianping_city_id(search_query)
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://www.dianping.com/search/keyword/{city_id}/0_{encoded_query}"
    text, final_url = request_meituan_text(url, referer="https://www.dianping.com/")
    if "account.dianping.com" in final_url or "pclogin" in final_url:
        return []
    stores = parse_dianping_shop_cards(text, final_url, search_query, terms)
    if not stores:
        for payload in extract_json_payloads_from_html(text):
            stores.extend(parse_meituan_json_payload(payload, final_url, search_query, terms, latitude, longitude))
    return select_top_stores(stores, terms, search_query, limit=3)


def parse_dianping_shop_cards(html_text: str, final_url: str, search_query: str, terms: list[str]) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    blocks = re.findall(r"<li[^>]*>(.*?)</li>", html_text, flags=re.DOTALL | re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"<div[^>]+class=[\"'][^\"']*(?:shop|txt|content)[^\"']*[\"'][^>]*>(.*?)</div>", html_text, flags=re.DOTALL | re.IGNORECASE)
    for index, block in enumerate(blocks[:60]):
        text = clean_html(block)
        if len(text) < 4:
            continue
        matched_terms = [term for term in terms if term and term in text]
        if not matched_terms and "美甲" not in text and "甲" not in text and "nail" not in text.lower():
            continue
        href_match = re.search(r"href=[\"']([^\"']*/shop/[^\"']+)[\"']", block, re.IGNORECASE)
        if not href_match:
            continue
        name = extract_dianping_shop_name(block, text)
        if not name or name.endswith(":") or name.endswith("：") or name in {"频道", "频道:"}:
            continue
        if is_excluded_store(name, text) or not is_nail_store(name, text):
            continue
        address = extract_labeled_text(text, ("地址", "商区")) or "地址以美团/点评门店页为准"
        rating = extract_rating_text(text)
        price = extract_price_text(text)
        photo_match = re.search(r"(?:src|data-src)=[\"']([^\"']+(?:jpg|jpeg|png|webp)[^\"']*)[\"']", block, re.IGNORECASE)
        photo = normalize_external_url(photo_match.group(1), final_url) if photo_match else ""
        url = normalize_external_url(href_match.group(1), final_url) if href_match else build_meituan_search_url(search_query)
        stores.append(
            {
                "id": f"dianping_live_{index}_{abs(hash(name))}",
                "name": name,
                "address": address,
                "distance": "",
                "tel": "",
                "type": "meituan_dianping_store",
                "rating": rating,
                "price": price,
                "photo": photo,
                "location": "",
                "source": "meituan_live_search",
                "externalUrl": url,
                "bookingUrl": url,
                "matchScore": score_store(name, text, matched_terms, rating),
                "matchReason": build_match_reason(matched_terms, []),
                "matchedTags": matched_terms or terms[:3],
                "caseImages": [photo] if photo else [],
                "searchQuery": search_query,
            }
        )
    return stores


def request_meituan_text(url: str, referer: str, accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8") -> tuple[str, str]:
    cookie = read_meituan_cookie()
    headers = {
        "User-Agent": first_env("MEITUAN_USER_AGENT")
        or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "KHTML, like Gecko Chrome/125.0 Safari/537.36",
        "Accept": accept,
        "Referer": referer,
        "Connection": "close",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers, method="GET")
    timeout = normalize_timeout(first_env("MEITUAN_TIMEOUT") or "8")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        charset_match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
        charset = charset_match.group(1) if charset_match else "utf-8"
        return body.decode(charset, errors="replace"), response.geturl()


def parse_meituan_json_payload(
    payload: Any,
    final_url: str,
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    collect_json_store_candidates(payload, stores, final_url, search_query, terms, latitude, longitude)
    return select_top_stores(stores, terms, search_query, limit=MEITUAN_PC_SEARCH_LIMIT)


def collect_json_store_candidates(
    node: Any,
    stores: list[dict[str, Any]],
    final_url: str,
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> None:
    if len(stores) >= 80:
        return
    if isinstance(node, dict):
        candidate = normalize_live_store(node, final_url, search_query, terms, latitude, longitude)
        if candidate:
            stores.append(candidate)
        for value in node.values():
            collect_json_store_candidates(value, stores, final_url, search_query, terms, latitude, longitude)
    elif isinstance(node, list):
        for item in node[:120]:
            collect_json_store_candidates(item, stores, final_url, search_query, terms, latitude, longitude)


def normalize_live_store(
    item: dict[str, Any],
    final_url: str,
    search_query: str,
    terms: list[str],
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any] | None:
    name = first_text(item, "title", "name", "shopName", "poiName", "displayName", "brandName")
    if not name or len(name) < 2 or len(name) > 80:
        return None

    haystack = flatten_text(item, 1600)
    if is_excluded_store(name, haystack) or not is_nail_store(name, haystack):
        return None
    matched_terms = [term for term in terms if term and term in haystack]
    looks_like_store = any(first_text(item, key) for key in ("address", "addr", "avgscore", "avgScore", "avgprice", "avgPrice", "poiId", "shopId"))
    looks_relevant = bool(matched_terms) or "美甲" in haystack or "nail" in haystack.lower() or "甲" in name
    if not looks_like_store or not looks_relevant:
        return None

    store_id = first_text(item, "poiId", "poiid", "shopId", "id", "dpShopId", "mtShopId")
    address = first_text(item, "address", "addr", "addrInfo", "areaName", "districtName")
    rating = first_text(item, "avgscore", "avgScore", "score", "rating", "star", "avgRating")
    price = first_text(item, "avgprice", "avgPrice", "avgPriceText", "price", "priceRange", "lowestprice", "lowestPrice")
    photo = first_text(item, "frontImg", "frontimg", "imageUrl", "imgUrl", "picUrl", "photo", "cover", "coverUrl")
    raw_url = first_text(item, "url", "detailUrl", "shopUrl", "href", "link")
    external_url = normalize_external_url(raw_url, final_url) or build_meituan_search_url(search_query)
    location = first_location(item)
    distance = first_text(item, "distance", "distanceText", "range", "walkDistance") or format_distance(latitude, longitude, location)
    case_images = collect_image_urls(item)
    score = score_store(name, haystack, matched_terms, rating)

    return {
        "id": store_id or f"meituan_live_{abs(hash(name + address))}",
        "name": name,
        "address": address or "地址以美团门店页为准",
        "distance": distance,
        "tel": "",
        "type": "meituan_live_store",
        "rating": rating,
        "price": price,
        "photo": photo or (case_images[0] if case_images else ""),
        "location": location,
        "source": "meituan_live_search",
        "externalUrl": external_url,
        "bookingUrl": external_url,
        "matchScore": score,
        "matchReason": build_match_reason(matched_terms, collect_case_titles(item)),
        "matchedTags": matched_terms or terms[:3],
        "caseImages": case_images[:4],
        "searchQuery": search_query,
    }


def extract_json_payloads_from_html(html_text: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", html_text, re.DOTALL | re.IGNORECASE):
        parsed = parse_json_text(match.group(1))
        if parsed is not None:
            payloads.append(parsed)

    for match in re.finditer(r"<script[^>]*>(.*?)</script>", html_text, re.DOTALL | re.IGNORECASE):
        script = match.group(1)
        if not any(token in script for token in ("poi", "shop", "merchant", "searchResult", "avgscore", "avgPrice")):
            continue
        for marker in ("window.__INITIAL_STATE__", "window.__DATA__", "window.__data__", "window.AppData", "__NEXT_DATA__"):
            index = script.find(marker)
            if index < 0:
                continue
            json_text = extract_balanced_json(script, index + len(marker))
            parsed = parse_json_text(json_text)
            if parsed is not None:
                payloads.append(parsed)
    return payloads


def parse_meituan_anchor_cards(html_text: str, final_url: str, search_query: str, terms: list[str]) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    for index, match in enumerate(re.finditer(r"<a\b([^>]*)>(.*?)</a>", html_text, re.DOTALL | re.IGNORECASE)):
        attrs = match.group(1)
        text = clean_html(match.group(2))
        if len(text) < 3 or len(text) > 160:
            continue
        matched_terms = [term for term in terms if term and term in text]
        if not matched_terms and "美甲" not in text and "nail" not in text.lower():
            continue
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, re.IGNORECASE)
        url = normalize_external_url(href_match.group(1), final_url) if href_match else build_meituan_search_url(search_query)
        name = text.splitlines()[0].strip()
        if is_excluded_store(name, text) or not is_nail_store(name, text):
            continue
        stores.append(
            {
                "id": f"meituan_anchor_{index}",
                "name": name,
                "address": "地址以美团门店页为准",
                "distance": "",
                "tel": "",
                "type": "meituan_live_store",
                "rating": "",
                "price": "",
                "photo": "",
                "location": "",
                "source": "meituan_live_search",
                "externalUrl": url,
                "bookingUrl": url,
                "matchScore": score_store(name, text, matched_terms, ""),
                "matchReason": build_match_reason(matched_terms, []),
                "matchedTags": matched_terms or terms[:3],
                "caseImages": [],
                "searchQuery": search_query,
            }
        )
    return stores


def normalize_snapshot_store(
    item: dict[str, Any],
    index: int,
    terms: list[str],
    search_query: str,
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any] | None:
    case_titles = item.get("caseTitles") if isinstance(item.get("caseTitles"), list) else []
    case_tags = item.get("caseTags") if isinstance(item.get("caseTags"), list) else item.get("tags")
    if not isinstance(case_tags, list):
        case_tags = []
    haystack = " ".join(
        str(part)
        for part in [
            item.get("name") or "",
            item.get("address") or "",
            item.get("description") or "",
            *case_titles,
            *case_tags,
        ]
    )
    matched_terms = [term for term in terms if term and term in haystack]
    if terms and not matched_terms:
        return None
    score = len(matched_terms) * 20 + min(len(case_titles), 4) * 3
    case_images = item.get("caseImages") if isinstance(item.get("caseImages"), list) else []
    location = str(item.get("location") or "")
    distance = str(item.get("distance") or "")
    if not distance:
        distance = format_distance(latitude, longitude, location)
    return {
        "id": str(item.get("id") or f"meituan_snapshot_{index}"),
        "name": str(item.get("name") or "美团门店"),
        "address": str(item.get("address") or "地址以美团门店页为准"),
        "distance": distance,
        "tel": str(item.get("tel") or ""),
        "type": "meituan_store_match",
        "rating": str(item.get("rating") or ""),
        "price": str(item.get("price") or item.get("priceRange") or ""),
        "photo": str(item.get("photo") or (case_images[0] if case_images else "")),
        "location": location,
        "source": "meituan_local_snapshot",
        "externalUrl": str(item.get("meituanUrl") or item.get("externalUrl") or build_meituan_search_url(search_query)),
        "bookingUrl": str(item.get("bookingUrl") or item.get("meituanUrl") or item.get("externalUrl") or ""),
        "matchScore": score,
        "matchReason": build_match_reason(matched_terms, case_titles),
        "matchedTags": matched_terms or terms[:3],
        "caseImages": [str(image) for image in case_images[:4]],
        "searchQuery": search_query,
    }


def fetch_live_search_handoff(search_query: str) -> dict[str, Any] | None:
    if not is_truthy(first_env("MEITUAN_ENABLE_LIVE_SEARCH")):
        return None
    cookie = read_meituan_cookie()
    if not cookie:
        return None
    url = build_meituan_search_url(search_query)
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "User-Agent": first_env("MEITUAN_USER_AGENT")
            or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "KHTML, like Gecko Chrome/125.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = getattr(response, "status", 200)
    except Exception:
        return None
    if status >= 400:
        return None
    return {
        "id": "meituan-signed-search",
        "name": f"美团登录搜索：{search_query}",
        "address": "后端已用本地登录态请求美团搜索页；打开后继续筛选有相似案例的门店。",
        "distance": "",
        "tel": "",
        "type": "meituan_signed_search",
        "rating": "",
        "price": "",
        "photo": "",
        "location": "",
        "source": "meituan_signed_search_handoff",
        "externalUrl": url,
        "bookingUrl": url,
        "matchScore": 0,
        "matchReason": "按已发布需求生成搜索词，并复用本机美团登录态进入搜索结果页。",
        "matchedTags": [],
        "caseImages": [],
        "searchQuery": search_query,
    }


def build_search_handoff_store(
    search_query: str,
    terms: list[str],
    demand: dict[str, Any] | None,
    latitude: float | None,
    longitude: float | None,
) -> dict[str, Any]:
    location = ""
    if longitude is not None and latitude is not None:
        location = f"{longitude},{latitude}"
    demand_text = str(demand.get("demandText") or "") if demand else ""
    reason = "、".join(terms[:4]) if terms else (demand_text[:24] or "美甲")
    return {
        "id": "meituan-demand-search",
        "name": f"美团搜索相似款：{search_query}",
        "address": "打开后先登录你的美团账号，再按相似案例、距离、价格和可约时间筛选门店。",
        "distance": "",
        "tel": "",
        "type": "meituan_search",
        "rating": "",
        "price": "",
        "photo": "",
        "location": location,
        "source": "meituan_search_handoff",
        "externalUrl": build_meituan_search_url(search_query),
        "bookingUrl": build_meituan_search_url(search_query),
        "matchScore": 0,
        "matchReason": f"已从需求单提取「{reason}」作为相似款搜索方向。",
        "matchedTags": terms,
        "caseImages": [],
        "searchQuery": search_query,
    }


def build_meituan_search_url(search_query: str) -> str:
    return build_dianping_search_url(search_query)


def build_dianping_search_url(search_query: str) -> str:
    city_id = resolve_dianping_city_id(search_query)
    encoded = urllib.parse.quote(search_query.strip() or "美甲")
    return f"https://www.dianping.com/search/keyword/{city_id}/0_{encoded}"


def build_local_store_open_url(name: str, search_query: str, direct_url: str) -> str:
    direct_url = normalize_store_direct_url(direct_url)
    params = {
        "storeName": name.strip(),
        "searchQuery": search_query.strip() or "美甲",
    }
    if direct_url:
        params["directUrl"] = direct_url
    return "/api/meituan/open-store?" + urllib.parse.urlencode(params)


def build_store_handoff_url(name: str, search_query: str, direct_url: str = "") -> str:
    name = name.strip()
    direct_url = normalize_store_direct_url(direct_url)
    if direct_url and direct_store_open_enabled():
        return direct_url
    if name:
        return build_dianping_search_url(f"{name} 美甲")
    return build_meituan_search_url(search_query)


def resolve_meituan_open_url(store_name: str, search_query: str, direct_url: str = "") -> str:
    direct_url = normalize_store_direct_url(direct_url)
    if direct_url and direct_store_open_enabled():
        return direct_url
    return build_store_handoff_url(store_name, search_query, direct_url)


def direct_store_open_enabled() -> bool:
    value = first_env("MEITUAN_OPEN_DIRECT_SHOP")
    if value is None or value == "":
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def normalize_store_direct_url(raw_url: str) -> str:
    url = html_lib.unescape(str(raw_url or "")).strip()
    for _ in range(16):
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url)
        is_local_open = parsed.path == "/api/meituan/open-store" or parsed.path.endswith("/api/meituan/open-store")
        if not is_local_open:
            break
        query = urllib.parse.parse_qs(parsed.query)
        next_url = query.get("directUrl", [""])[0].strip()
        if not next_url or next_url == url:
            return ""
        url = next_url
    if url.startswith("/api/meituan/open-store"):
        return ""
    normalized = normalize_external_url(url, "https://www.meituan.com/")
    if not normalized.startswith(("http://", "https://")):
        return ""
    if "/api/meituan/open-store" in urllib.parse.urlparse(normalized).path:
        return ""
    return normalized


def apply_store_booking_handoff(store: dict[str, Any], search_query: str) -> None:
    source = str(store.get("source") or "")
    if not (source.startswith("meituan_live_search") or source.startswith("meituan_playwright_search")):
        return
    name = str(store.get("name") or "").strip()
    if not name:
        return
    direct_url = str(store.get("rawExternalUrl") or store.get("bookingUrl") or store.get("externalUrl") or "")
    direct_url = normalize_store_direct_url(direct_url)
    store["rawExternalUrl"] = direct_url
    store["safeExternalUrl"] = build_store_handoff_url(name, search_query, direct_url)
    store["externalUrl"] = build_local_store_open_url(name, search_query, direct_url)
    store["bookingUrl"] = store["externalUrl"]


def live_search_enabled() -> bool:
    value = first_env("MEITUAN_ENABLE_LIVE_SEARCH")
    if not value:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def playwright_search_enabled() -> bool:
    value = first_env("MEITUAN_ENABLE_PLAYWRIGHT_SEARCH")
    if not value:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def resolve_meituan_city_id(search_query: str) -> str:
    configured = first_env("MEITUAN_CITY_ID")
    if configured:
        return configured
    city_map = {
        "合肥": "110",
        "蜀山": "110",
        "杭州": "50",
        "滨江": "50",
        "上海": "10",
        "北京": "1",
        "广州": "20",
        "深圳": "30",
        "南京": "55",
        "成都": "59",
        "武汉": "57",
        "西安": "42",
        "苏州": "52",
    }
    for name, city_id in city_map.items():
        if name in search_query:
            return city_id
    return DEFAULT_MEITUAN_CITY_ID


def resolve_dianping_city_id(search_query: str) -> str:
    configured = first_env("DIANPING_CITY_ID", "MEITUAN_DIANPING_CITY_ID")
    if configured:
        return configured
    city_map = {
        "合肥": "110",
        "蜀山": "110",
        "上海": "1",
        "北京": "2",
        "杭州": "3",
        "滨江": "3",
        "广州": "4",
        "南京": "5",
        "苏州": "6",
        "深圳": "7",
        "成都": "8",
        "武汉": "16",
        "西安": "17",
    }
    for name, city_id in city_map.items():
        if name in search_query:
            return city_id
    return "110" if hefei_shushan_store_pool_enabled() else "3"


def select_top_stores(
    stores: list[dict[str, Any]],
    terms: list[str],
    search_query: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for store in stores:
        name = str(store.get("name") or "").strip()
        if not name:
            continue
        address = str(store.get("address") or "").strip()
        key = canonical_store_key(store, name, address)
        haystack = flatten_text(store, 1200)
        matched_terms = store.get("matchedTags") if isinstance(store.get("matchedTags"), list) else []
        normalized_terms = [str(term) for term in matched_terms if str(term).strip()]
        if not normalized_terms:
            normalized_terms = [term for term in terms if term and term in haystack]
            store["matchedTags"] = normalized_terms
        if normalized_terms and str(store.get("matchReason") or "").startswith("可在"):
            store["matchReason"] = build_match_reason(normalized_terms, [])
        elif not store.get("matchReason"):
            store["matchReason"] = build_match_reason(normalized_terms, [])
        store["externalUrl"] = str(store.get("externalUrl") or build_meituan_search_url(search_query))
        store["bookingUrl"] = str(store.get("bookingUrl") or store.get("externalUrl"))
        apply_store_booking_handoff(store, search_query)
        current_score = safe_float(store.get("matchScore"))
        if current_score is None:
            store["matchScore"] = score_store(name, haystack, normalized_terms, str(store.get("rating") or ""))
        if key not in deduped or float(store.get("matchScore") or 0) > float(deduped[key].get("matchScore") or 0):
            deduped[key] = store
    ranked = sorted(
        deduped.values(),
        key=lambda store: (
            float(store.get("matchScore") or 0),
            safe_float(store.get("rating")) or 0,
            1 if store.get("photo") or store.get("caseImages") else 0,
        ),
        reverse=True,
    )
    return ranked[:limit]


def canonical_store_key(store: dict[str, Any], name: str, address: str) -> str:
    direct_url = normalize_store_direct_url(str(store.get("rawExternalUrl") or store.get("externalUrl") or store.get("bookingUrl") or ""))
    if direct_url:
        parsed = urllib.parse.urlparse(direct_url)
        match = re.search(r"/shop/([^/?#]+)", parsed.path)
        if match:
            return f"shop:{match.group(1).lower()}"

    clean_name = re.sub(r"[\s\-_/·・,，.。()（）]+", "", name).lower()
    clean_address = re.sub(r"[\s\-_/·・,，.。()（）]+", "", address).lower()
    if clean_address and "地址以" not in clean_address and "地址未" not in clean_address:
        return f"name-address:{clean_name}|{clean_address[:24]}"
    return f"name:{clean_name}"


def parse_json_text(text: str | None) -> Any | None:
    if not text:
        return None
    cleaned = html_lib.unescape(text).strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Some pages escape JSON into quoted JavaScript strings.
    try:
        unquoted = json.loads(f'"{cleaned}"')
        if isinstance(unquoted, str):
            return json.loads(unquoted)
    except json.JSONDecodeError:
        return None
    return None


def extract_balanced_json(script: str, start_index: int) -> str:
    cursor = start_index
    while cursor < len(script) and script[cursor] in " \t\r\n=:":
        cursor += 1
    while cursor < len(script) and script[cursor] in " \t\r\n(":
        cursor += 1
    if cursor >= len(script) or script[cursor] not in "{[":
        return ""

    opener = script[cursor]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(cursor, len(script)):
        char = script[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return script[cursor : index + 1]
    return ""


def clean_html(value: str) -> str:
    no_script = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def extract_dianping_shop_name(block: str, fallback_text: str) -> str:
    patterns = (
        r"<h4[^>]*>(.*?)</h4>",
        r"class=[\"'][^\"']*shop-name[^\"']*[\"'][^>]*>(.*?)<",
        r"title=[\"']([^\"']{2,60})[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, block, re.DOTALL | re.IGNORECASE)
        if match:
            name = clean_html(match.group(1))
            if 2 <= len(name) <= 60:
                return name
    for part in re.split(r"\s{2,}|地址|人均|评分|星级", fallback_text):
        text = part.strip(" -·|")
        if 2 <= len(text) <= 60:
            return text
    return ""


def extract_labeled_text(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[:：]?\s*([^人均评分电话]+)", text)
        if match:
            value = match.group(1).strip(" -·|")
            if value:
                return value[:80]
    return ""


def extract_rating_text(text: str) -> str:
    for pattern in (r"(\d(?:\.\d)?)\s*分", r"评分[:：]?\s*(\d(?:\.\d)?)", r"star-(\d+)"):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1)
        if value.isdigit() and len(value) > 1:
            return f"{int(value) / 10:.1f}"
        return value
    return ""


def extract_price_text(text: str) -> str:
    match = re.search(r"(?:人均|¥|￥)\s*([0-9]{1,5}(?:\.[0-9])?)", text)
    return match.group(1) if match else ""


def first_text(item: dict[str, Any], *keys: str) -> str:
    lowered = {key.lower(): key for key in item.keys()}
    for key in keys:
        actual_key = lowered.get(key.lower())
        if not actual_key:
            continue
        value = item.get(actual_key)
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return html_lib.unescape(text)
    return ""


def flatten_text(value: Any, max_len: int = 2000) -> str:
    parts: list[str] = []

    def visit(node: Any) -> None:
        if sum(len(part) for part in parts) >= max_len:
            return
        if isinstance(node, dict):
            for next_value in node.values():
                visit(next_value)
        elif isinstance(node, list):
            for next_value in node[:80]:
                visit(next_value)
        elif isinstance(node, (str, int, float)):
            text = str(node).strip()
            if text:
                parts.append(text)

    visit(value)
    return " ".join(parts)[:max_len]


def normalize_external_url(raw_url: str, base_url: str) -> str:
    if not raw_url:
        return ""
    url = html_lib.unescape(raw_url).strip()
    if not url or url.startswith("javascript:"):
        return ""
    if url.startswith("//"):
        return "https:" + url
    return urllib.parse.urljoin(base_url, url)


def first_location(item: dict[str, Any]) -> str:
    location = first_text(item, "location", "latlng", "lnglat")
    if location and "," in location:
        return location
    longitude = first_text(item, "longitude", "lng", "lon", "x")
    latitude = first_text(item, "latitude", "lat", "y")
    if longitude and latitude:
        return f"{longitude},{latitude}"
    return ""


def collect_image_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(node: Any) -> None:
        if len(urls) >= 8:
            return
        if isinstance(node, dict):
            for next_value in node.values():
                visit(next_value)
        elif isinstance(node, list):
            for next_value in node[:80]:
                visit(next_value)
        elif isinstance(node, str):
            url = node.strip()
            if not url:
                return
            if url.startswith("//"):
                url = "https:" + url
            looks_like_image = (
                "p0.meituan.net" in url
                or "p1.meituan.net" in url
                or "p.meituan.net" in url
                or bool(re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.IGNORECASE))
            )
            if looks_like_image and url not in urls:
                urls.append(url)

    visit(value)
    return urls


def collect_case_titles(value: Any) -> list[str]:
    titles: list[str] = []

    def visit(node: Any) -> None:
        if len(titles) >= 8:
            return
        if isinstance(node, dict):
            for key, next_value in node.items():
                if key.lower() in {"title", "name", "casetitle", "productname", "dealtitle"} and isinstance(next_value, str):
                    text = next_value.strip()
                    if text and text not in titles:
                        titles.append(text)
                else:
                    visit(next_value)
        elif isinstance(node, list):
            for next_value in node[:80]:
                visit(next_value)

    visit(value)
    return titles


def score_store(name: str, haystack: str, matched_terms: list[str], rating: str) -> float:
    score = 0.0
    score += len(set(matched_terms)) * 25
    if "美甲" in name:
        score += 35
    elif "甲" in name or "nail" in name.lower():
        score += 20
    if "美甲" in haystack:
        score += 12
    if any(word in haystack for word in ("可预约", "团购", "门店", "商户", "人均")):
        score += 8
    parsed_rating = safe_float(rating)
    if parsed_rating is not None:
        score += min(parsed_rating, 5) * 4
    if len(name) > 45:
        score -= 12
    return score


def is_nail_store(name: str, haystack: str) -> bool:
    combined = f"{name} {haystack}".lower()
    return any(keyword in combined for keyword in ("美甲", "美睫", "nail", "指甲", "甲艺", "甲片", "手足护理", "美手"))


def is_nail_store_name(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in ("美甲", "美睫", "nail", "指甲", "甲艺", "甲片", "美手"))


def is_excluded_store(name: str, haystack: str) -> bool:
    combined = f"{name} {haystack}"
    excluded_keywords = (
        "医院",
        "医疗",
        "医美",
        "整形",
        "皮肤科",
        "植发",
        "口腔",
        "牙科",
        "产后",
        "月子",
        "瘦身",
        "祛斑",
        "抗衰",
        "玻尿酸",
        "水光针",
    )
    return any(keyword in combined for keyword in excluded_keywords)


def normalize_timeout(value: str) -> int:
    try:
        timeout = int(float(value))
    except (TypeError, ValueError):
        return 8
    return max(3, min(timeout, 20))


def read_meituan_cookie() -> str:
    env_cookie = first_env(*MEITUAN_COOKIE_KEYS)
    if env_cookie:
        return env_cookie
    session_file = resolve_meituan_session_path()
    if not session_file or not session_file.exists():
        return ""
    try:
        parsed = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(parsed, dict):
        direct_cookie = parsed.get("cookie") or parsed.get("Cookie")
        if isinstance(direct_cookie, str) and direct_cookie.strip():
            return direct_cookie.strip()
        cookies = parsed.get("cookies")
        if isinstance(cookies, list):
            parts = []
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue
                name = str(cookie.get("name") or "").strip()
                value = str(cookie.get("value") or "").strip()
                domain = str(cookie.get("domain") or "")
                if name and value and ("meituan" in domain or "dianping" in domain or not domain):
                    parts.append(f"{name}={value}")
            return "; ".join(parts)
    return ""


def resolve_meituan_session_path() -> Path:
    configured = resolve_optional_path(first_env(*MEITUAN_SESSION_FILE_KEYS))
    return configured or (PROJECT_ROOT / "backend" / ".meituan_session.json")


def resolve_optional_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def build_match_reason(matched_terms: list[str], case_titles: list[Any]) -> str:
    if matched_terms:
        return f"门店案例包含相似元素：{'、'.join(matched_terms[:5])}。"
    if case_titles:
        return f"门店有 {len(case_titles)} 条美甲案例，可继续核对相似款。"
    return "可在美团门店页继续核对案例图和可约时间。"


def add_term(terms: list[str], raw: str) -> None:
    term = raw.strip().strip("#,，.。;； ")
    if not term or len(term) > 16:
        return
    if term not in terms:
        terms.append(term)


def unique_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        add_term(result, value)
    return result


def is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None


def format_distance(latitude: float | None, longitude: float | None, location: str) -> str:
    if latitude is None or longitude is None or not location:
        return ""
    parts = location.split(",")
    if len(parts) != 2:
        return ""
    store_lng = safe_float(parts[0])
    store_lat = safe_float(parts[1])
    if store_lat is None or store_lng is None:
        return ""
    meters = haversine_meters(latitude, longitude, store_lat, store_lng)
    if meters >= 1000:
        return f"{meters / 1000:.1f}km"
    return f"{int(round(meters))}m"


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
