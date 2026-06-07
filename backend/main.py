from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from .doubao_client import analyze_hand_image, first_env, get_doubao_status, load_env
    from .finger_labeler import label_fingers
    from .meituan_store_matcher import (
        build_dianping_search_url,
        build_hefei_shushan_style_store_response,
        build_meituan_store_response,
        get_meituan_status,
        resolve_meituan_open_url,
    )
    from .prompts.demand_text_prompt import DEMAND_TEXT_PROMPT
    from .tryon_optimizer import (
        generate_nail_style,
        generate_nail_tryon_v2,
        get_tryon_optimizer_status,
        load_ui_prompt_plan_bundle,
        optimize_tryon_image,
        transfer_nail_style,
    )
    from . import xhs_bridge
except ImportError:
    from doubao_client import analyze_hand_image, first_env, get_doubao_status, load_env
    from finger_labeler import label_fingers
    from meituan_store_matcher import (
        build_dianping_search_url,
        build_hefei_shushan_style_store_response,
        build_meituan_store_response,
        get_meituan_status,
        resolve_meituan_open_url,
    )
    from prompts.demand_text_prompt import DEMAND_TEXT_PROMPT
    from tryon_optimizer import (
        generate_nail_style,
        generate_nail_tryon_v2,
        get_tryon_optimizer_status,
        load_ui_prompt_plan_bundle,
        optimize_tryon_image,
        transfer_nail_style,
    )
    import xhs_bridge


app = FastAPI(title="NailVerse API", version="0.1.0")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NAIL_MAIN_DIR = PROJECT_ROOT / "Nail-main"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "null",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if NAIL_MAIN_DIR.exists():
    app.mount("/nail-main", StaticFiles(directory=NAIL_MAIN_DIR, html=True), name="nail-main")


@app.get("/")
def root():
    return {"service": "NailVerse API", "health": "/api/health"}


class OptimizeTryOnRequest(BaseModel):
    image: str


class TransferNailStyleRequest(BaseModel):
    hand_image: str
    style_image: str
    draft_tryon_image: Optional[str] = None
    debug: bool = False


class GenerateNailTryOnV2Request(BaseModel):
    hand_image: str
    style_image: str
    style_id: Optional[str] = None
    styleId: Optional[str] = None
    style_name: Optional[str] = None
    styleName: Optional[str] = None
    bti_result: Optional[dict[str, Any]] = None
    fast_mode: bool = True
    length_mode: str = "match_reference"
    lengthMode: Optional[str] = None


class LabelFingersRequest(BaseModel):
    image: str
    title: str = ""


class GenerateNailStyleRequest(BaseModel):
    prompt: str
    btiResult: Optional[dict[str, Any]] = None
    handImage: Optional[str] = None


class GenerateDemandTextRequest(BaseModel):
    style: Optional[dict[str, Any]] = None
    btiResult: Optional[dict[str, Any]] = None
    budgetRange: str = "150-250元"
    location: str = "合肥蜀山区"
    availableTime: str = "周末下午"
    acceptModification: bool = True


class DemandRequest(BaseModel):
    userId: Optional[str] = None
    referenceImage: Optional[str] = None
    referenceLink: Optional[str] = None
    sourceType: str = "ai_generated"
    styleTags: list[str] = []
    btiCode: Optional[str] = None
    btiArchetype: Optional[str] = None
    fitInfo: Optional[str] = None
    budgetRange: Optional[str] = None
    location: Optional[str] = None
    availableTime: Optional[str] = None
    acceptModification: bool = True
    demandText: str
    status: str = "published"


class NearbyStoresRequest(BaseModel):
    latitude: float
    longitude: float
    keyword: str = "美甲"
    radius: int = 3000
    demandId: Optional[str] = None
    platform: str = "amap"


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/model-status")
def model_status():
    return {
        "handAnalysis": get_doubao_status(),
        "tryOnOptimizer": get_tryon_optimizer_status(),
        "meituanStoreSearch": get_meituan_status(),
    }


@app.get("/api/xhs/trends/latest")
def xhs_trends_latest():
    try:
        return xhs_bridge.build_frontend_payload()
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/api/xhs/sync/start")
def xhs_sync_start():
    return xhs_bridge.start_sync()


@app.get("/api/xhs/sync/status")
def xhs_sync_status():
    return xhs_bridge.get_sync_status()


@app.get("/api/nail-style-prompt-plans")
def nail_style_prompt_plans(styleId: str = "", styleName: str = "", full: bool = False):
    bundle = load_ui_prompt_plan_bundle()
    if not bundle:
        raise HTTPException(status_code=404, detail="本地款式提示词 bundle 不存在")
    if styleId:
        item = (bundle.get("byId") or {}).get(styleId)
        if not item:
            raise HTTPException(status_code=404, detail="未找到该 styleId 的提示词")
        return {"success": True, "style": item}
    if styleName:
        item = (bundle.get("byName") or {}).get(styleName)
        if not item:
            raise HTTPException(status_code=404, detail="未找到该 styleName 的提示词")
        return {"success": True, "style": item}
    if full:
        return {"success": True, "bundle": bundle}
    return {
        "success": True,
        "schemaVersion": bundle.get("schemaVersion"),
        "total": bundle.get("total"),
        "styles": [
            {
                "order": item.get("order"),
                "uiId": item.get("uiId"),
                "uiName": item.get("uiName"),
                "uiImage": item.get("uiImage"),
                "uiTags": item.get("uiTags"),
                "overallStyle": item.get("overallStyle"),
                "schemaVersion": item.get("schemaVersion"),
                "sourceFile": item.get("sourceFile"),
            }
            for item in (bundle.get("styles") or [])
            if isinstance(item, dict)
        ],
    }


@app.post("/api/analyze-hand")
async def analyze_hand(image: UploadFile = File(...)):
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")

    content_type = image.content_type or "image/png"
    try:
        return analyze_hand_image(image_bytes, content_type)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"AI hand analysis failed: {error}") from error


@app.post("/api/optimize-tryon")
def optimize_tryon(payload: OptimizeTryOnRequest):
    if not payload.image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="image must be a data URL")

    return {"image": optimize_tryon_image(payload.image)}


@app.post("/api/transfer-nail-style")
def transfer_nail_style_endpoint(payload: TransferNailStyleRequest):
    if not payload.hand_image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="hand_image must be a data URL")
    if not payload.style_image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="style_image must be a data URL")
    if payload.draft_tryon_image and not payload.draft_tryon_image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="draft_tryon_image must be a data URL")

    result = transfer_nail_style(payload.hand_image, payload.style_image, payload.draft_tryon_image)
    if payload.debug:
        result["debug_image"] = payload.draft_tryon_image or payload.hand_image
    return result


@app.post("/api/label-fingers")
def label_fingers_endpoint(payload: LabelFingersRequest):
    if not payload.image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="image must be a data URL")
    return label_fingers(payload.image, payload.title)


@app.post("/api/generate-nail-tryon-v2")
def generate_nail_tryon_v2_endpoint(payload: GenerateNailTryOnV2Request):
    if not payload.hand_image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="hand_image must be a data URL")
    if not payload.style_image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="style_image must be a data URL")

    return generate_nail_tryon_v2(
        payload.hand_image,
        payload.style_image,
        payload.bti_result,
        payload.fast_mode,
        payload.lengthMode or payload.length_mode,
        None,
        payload.styleId or payload.style_id,
        payload.styleName or payload.style_name,
    )


@app.post("/api/generate-nail-style")
def generate_nail_style_endpoint(payload: GenerateNailStyleRequest):
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if payload.handImage and not payload.handImage.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="handImage must be a data URL")

    return generate_nail_style(prompt, payload.btiResult, payload.handImage)


@app.post("/api/generate-demand-text")
def generate_demand_text_endpoint(payload: GenerateDemandTextRequest):
    style = payload.style or {}
    name = str(style.get("name") or "这款美甲")
    tags = style.get("tags") if isinstance(style.get("tags"), list) else []
    tag_text = "、".join(str(tag) for tag in tags[:4] if tag)
    modification = "也接受轻微改款。" if payload.acceptModification else "希望尽量按参考图还原。"
    time_text = payload.availableTime.strip()
    if time_text and "可约" not in time_text:
        time_text = f"{time_text}可约"
    location_text = payload.location.strip()
    if location_text and not any(keyword in location_text for keyword in ("附近", "周边", "公里", "米", "内")):
        location_text = f"{location_text}附近"
    text = (
        f"想做{name}"
        f"{'，偏' + tag_text if tag_text else ''}。"
        f"预算{payload.budgetRange}，{time_text}，位置在{location_text}。"
        f"{modification}"
    )
    return {"demandText": text[:80], "promptTemplate": DEMAND_TEXT_PROMPT.strip()}


@app.post("/api/demands")
def create_demand(payload: DemandRequest):
    demand_id = f"demand_{int(time.time() * 1000)}"
    demand = {
        **payload.model_dump(),
        "id": demand_id,
        "status": payload.status if payload.status in {"draft", "published", "quoted", "booked"} else "published",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    existing = read_demands()
    existing.append(demand)
    write_demands(existing)
    store_search = run_store_search_for_demand(demand)
    return {"success": True, "demandId": demand_id, "storeSearch": store_search}


@app.get("/api/demands/{demand_id}")
def get_demand(demand_id: str):
    demand = find_demand(demand_id)
    if not demand:
        raise HTTPException(status_code=404, detail="需求单不存在")
    return {"success": True, "demand": demand}


@app.get("/api/meituan-status")
def meituan_status():
    return get_meituan_status()


@app.get("/api/meituan/open-store")
def open_meituan_store(storeName: str = "", searchQuery: str = "美甲", directUrl: str = ""):
    load_env()
    target_url = resolve_meituan_open_url(storeName, searchQuery, directUrl)
    return RedirectResponse(target_url, status_code=302)


@app.get("/api/style-store-recommendations")
def style_store_recommendations(styleId: str = "", styleName: str = "", limit: int = 3):
    if not styleId.strip() and not styleName.strip():
        raise HTTPException(status_code=400, detail="styleId or styleName is required")
    return build_hefei_shushan_style_store_response(styleId, styleName, limit)


@app.post("/api/nearby-stores")
def nearby_stores(payload: NearbyStoresRequest):
    load_env()
    demand = None
    if payload.demandId:
        demand = find_demand(payload.demandId)
        if not demand:
            raise HTTPException(status_code=404, detail="需求单不存在，请先重新发布需求。")

    if payload.demandId or payload.platform.strip().lower() == "meituan":
        result = build_meituan_store_response(payload.model_dump(), demand)
        if payload.demandId:
            save_store_search(payload.demandId, result)
        return result

    api_key = first_env("AMAP_WEB_SERVICE_KEY", "AMAP_API_KEY", "GAODE_API_KEY")
    if not api_key:
        stores = public_store_search_fallback(payload)
        return {
            "success": True,
            "source": "public_search_fallback",
            "stores": stores,
            "count": len(stores),
            "message": "未配置 AMAP_WEB_SERVICE_KEY，已返回公开搜索入口；不会使用个人账号密码抓取平台数据。",
        }

    radius = max(500, min(int(payload.radius), 10000))
    params = {
        "key": api_key,
        "keywords": payload.keyword.strip() or "美甲",
        "location": f"{payload.longitude},{payload.latitude}",
        "radius": str(radius),
        "offset": "20",
        "page": "1",
        "extensions": "all",
    }
    url = "https://restapi.amap.com/v3/place/around?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            raw = response.read().decode("utf-8")
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实门店数据获取失败：{error}") from error

    data = json.loads(raw)
    if str(data.get("status")) != "1":
        detail = data.get("info") or "真实门店数据源返回失败"
        raise HTTPException(status_code=502, detail=detail)

    stores = []
    for poi in data.get("pois") or []:
        biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
        photos = poi.get("photos") if isinstance(poi.get("photos"), list) else []
        photo_url = ""
        if photos and isinstance(photos[0], dict):
            photo_url = str(photos[0].get("url") or "")
        stores.append(
            {
                "id": str(poi.get("id") or ""),
                "name": str(poi.get("name") or ""),
                "address": str(poi.get("address") or ""),
                "distance": str(poi.get("distance") or ""),
                "tel": str(poi.get("tel") or ""),
                "type": str(poi.get("type") or ""),
                "rating": str(biz_ext.get("rating") or ""),
                "price": str(biz_ext.get("cost") or ""),
                "photo": photo_url,
                "location": str(poi.get("location") or ""),
                "source": "amap_place_around",
            }
        )

    return {
        "success": True,
        "source": "amap_place_around",
        "stores": stores,
        "count": len(stores),
    }


def run_store_search_for_demand(demand: dict[str, Any]) -> dict[str, Any]:
    payload = default_meituan_store_payload(demand)
    try:
        result = build_meituan_store_response(payload, demand)
    except Exception as error:
        result = {
            "success": False,
            "source": "meituan_auto_search_failed",
            "stores": [],
            "count": 0,
            "message": f"后台美团搜索失败：{error}",
            "searchQuery": "美甲",
            "matchedTerms": [],
            "meituanSession": get_meituan_status(),
        }
    save_store_search(str(demand.get("id") or ""), result)
    return result


def default_meituan_store_payload(demand: dict[str, Any]) -> dict[str, Any]:
    load_env()
    latitude = env_float("MEITUAN_DEFAULT_LATITUDE") or env_float("DEFAULT_LATITUDE") or 30.2084
    longitude = env_float("MEITUAN_DEFAULT_LONGITUDE") or env_float("DEFAULT_LONGITUDE") or 120.212
    keyword = str(first_env("MEITUAN_DEFAULT_KEYWORD") or "美甲").strip() or "美甲"
    return {
        "latitude": latitude,
        "longitude": longitude,
        "keyword": keyword,
        "radius": 3000,
        "demandId": str(demand.get("id") or ""),
        "platform": "meituan",
    }


def env_float(key: str) -> float | None:
    value = first_env(key)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def store_searches_db_path() -> Path:
    db_dir = Path(__file__).with_name("mock_db")
    db_dir.mkdir(exist_ok=True)
    return db_dir / "store_searches.json"


def read_store_searches() -> dict[str, Any]:
    db_path = store_searches_db_path()
    if not db_path.exists():
        return {}
    try:
        parsed = json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_store_searches(searches: dict[str, Any]) -> None:
    store_searches_db_path().write_text(json.dumps(searches, ensure_ascii=False, indent=2), encoding="utf-8")


def save_store_search(demand_id: str, result: dict[str, Any]) -> None:
    if not demand_id:
        return
    searches = read_store_searches()
    searches[demand_id] = {
        **result,
        "demandId": demand_id,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_store_searches(searches)


def public_store_search_fallback(payload: NearbyStoresRequest) -> list[dict[str, str]]:
    keyword = payload.keyword.strip() or "美甲"
    center = f"{payload.longitude},{payload.latitude}"
    radius = str(max(500, min(int(payload.radius), 10000)))
    return [
        {
            "id": "meituan-public-search",
            "name": f"美团公开搜索：附近{keyword}",
            "address": "打开美团公开搜索页后，可使用你浏览器自己的登录状态和定位查看附近门店。",
            "distance": "",
            "tel": "",
            "type": "public_search",
            "rating": "",
            "price": "",
            "photo": "",
            "location": center,
            "source": "public_search_fallback",
            "externalUrl": build_dianping_search_url(keyword),
        },
        {
            "id": "amap-public-search",
            "name": f"高德地图搜索：附近{keyword}",
            "address": "无需后台账号；打开地图后按当前位置筛选门店。",
            "distance": "",
            "tel": "",
            "type": "public_search",
            "rating": "",
            "price": "",
            "photo": "",
            "location": center,
            "source": "public_search_fallback",
            "externalUrl": (
                "https://uri.amap.com/search?"
                + urllib.parse.urlencode(
                    {
                        "keyword": keyword,
                        "center": center,
                        "radius": radius,
                        "src": "nail-alpha",
                    }
                )
            ),
        },
    ]


def demands_db_path() -> Path:
    db_dir = Path(__file__).with_name("mock_db")
    db_dir.mkdir(exist_ok=True)
    return db_dir / "demands.json"


def read_demands() -> list[dict[str, Any]]:
    db_path = demands_db_path()
    if not db_path.exists():
        return []
    try:
        parsed = json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def write_demands(demands: list[dict[str, Any]]) -> None:
    demands_db_path().write_text(json.dumps(demands, ensure_ascii=False, indent=2), encoding="utf-8")


def find_demand(demand_id: str) -> dict[str, Any] | None:
    for demand in read_demands():
        if str(demand.get("id") or "") == demand_id:
            return demand
    return None
