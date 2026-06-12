from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
XHS_ROOT = PROJECT_ROOT / "xhs"
RUNS_DIR = XHS_ROOT / "runs"
CRAWLER = XHS_ROOT / "main.py"
TREND_ASSET_DIR = PROJECT_ROOT / "Nail-main" / "public" / "assets" / "xhs-trends" / "latest"
FALLBACK_TREND_IMAGE_COUNT = 8

sync_lock = threading.Lock()
sync_state: dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "step": "等待同步",
    "startedAt": None,
    "finishedAt": None,
    "runId": None,
    "error": None,
    "logs": [],
}
sync_process: subprocess.Popen[str] | None = None


def set_state(**updates: object) -> None:
    with sync_lock:
        sync_state.update(updates)


def get_sync_status() -> dict[str, Any]:
    with sync_lock:
        return dict(sync_state)


def append_log(line: str) -> None:
    text = line.strip()
    if not text:
        return
    with sync_lock:
        logs = sync_state.setdefault("logs", [])
        logs.append(text)
        del logs[:-80]
        if "浏览器已打开" in text or "自动检测" in text:
            sync_state.update(progress=max(sync_state["progress"], 12), step="正在检测小红书登录状态")
        elif "发现候选" in text or "轮滚动" in text:
            sync_state.update(progress=max(sync_state["progress"], 30), step="正在采集热门候选笔记")
        elif "补充详情" in text:
            sync_state.update(progress=max(sync_state["progress"], 48), step="正在读取笔记详情")
        elif "筛选图片" in text:
            sync_state.update(progress=max(sync_state["progress"], 66), step="正在筛选完整美甲图片")
        elif "入选" in text:
            sync_state.update(progress=max(sync_state["progress"], 76), step="正在同步入选图片")
        elif "trend_summary.json" in text:
            sync_state.update(progress=max(sync_state["progress"], 92), step="正在生成趋势总结")


def latest_run_dir() -> Path | None:
    if not RUNS_DIR.exists():
        return None
    runs = [
        path
        for path in RUNS_DIR.iterdir()
        if path.is_dir() and (path / "hot_nails.json").exists() and (path / "trend_summary.json").exists()
    ]
    if not runs:
        return None
    return max(runs, key=lambda path: path.stat().st_mtime)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_number(value: int | float | None) -> str:
    number = int(value or 0)
    if number >= 10000:
        return f"{round(number / 10000, 1)}w"
    return str(number)


def as_date(value: str | None) -> str:
    return value[:10] if value else ""


def resolve_item_image_path(item: dict[str, Any], run: Path) -> tuple[str, Path | None]:
    src_text = str(item.get("image_path") or "")
    if not src_text:
        return "", None
    src = Path(src_text)
    if src.exists():
        return src_text, src
    filename = PureWindowsPath(src_text).name or Path(src_text).name
    local = run / "images" / filename
    if local.exists():
        return src_text, local
    return src_text, None


def copy_latest_images(items: list[dict[str, Any]], run: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        TREND_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return mapping

    for index, item in enumerate(items, start=1):
        src_key, src = resolve_item_image_path(item, run)
        if not src_key or not src:
            continue
        dest_name = f"xhs_latest_{index:02d}{src.suffix or '.webp'}"
        dest = TREND_ASSET_DIR / dest_name
        try:
            shutil.copy2(src, dest)
        except OSError:
            continue
        mapping[src_key] = f"xhs-trends/latest/{dest_name}"
    return mapping


def fallback_trend_image(index: int) -> str:
    fallback_index = ((index - 1) % FALLBACK_TREND_IMAGE_COUNT) + 1
    return f"xhs-trends/latest/xhs_latest_{fallback_index:02d}.webp"


def merchant_label(item: dict[str, Any], index: int) -> tuple[str, list[str], str, str]:
    tags = item.get("style_tags") or []
    fallback_tags = [
        ["甜妹", "蝴蝶结", "春夏显白"],
        ["多巴胺渐变", "显白", "夏日"],
        ["清透", "短甲", "通勤"],
        ["花朵晕染", "温柔", "奶油感"],
        ["多巴胺", "淡人", "低饱和"],
        ["花朵晕染", "拍照", "细节感"],
        ["冰感清透", "蓝色系", "韩系"],
        ["内容种草", "搭配", "拍照"],
    ]
    display_tags = tags[:3] if tags else fallback_tags[(index - 1) % len(fallback_tags)]
    label = display_tags[0] if display_tags else "趋势款"
    merchant_title = f"{'上涨机会' if index <= 3 else '趋势机会'}：{label}"
    signal = f"小红书 {compact_number(item.get('likes'))} 点赞 · 近 {item.get('days_old', '-')} 天"
    action = f"建议上新{label}案例图，搭配{' / '.join(display_tags[:2])}做门店套餐。"
    tag_text = "".join(display_tags)
    if "多巴胺" in tag_text:
        action = "建议拆成拍照版和通勤版两套报价，避免只热不约。"
    elif "清透" in tag_text or "显白" in tag_text:
        action = "建议作为春夏显白主推款，补齐短甲和长甲两个展示版本。"
    return merchant_title, display_tags, signal, action


def build_frontend_payload() -> dict[str, Any]:
    run = latest_run_dir()
    if not run:
        raise FileNotFoundError("没有找到 xhs/runs 输出目录")
    hot_path = run / "hot_nails.json"
    trend_path = run / "trend_summary.json"
    if not hot_path.exists() or not trend_path.exists():
        raise FileNotFoundError(f"{run.name} 缺少 hot_nails.json 或 trend_summary.json")

    hot = read_json(hot_path)
    summary = read_json(trend_path)
    items = hot.get("items", [])
    image_mapping = copy_latest_images(items, run)

    trends = []
    for index, item in enumerate(items, start=1):
        merchant_title, tags, signal, action = merchant_label(item, index)
        image_path = str(item.get("image_path") or "")
        trends.append(
            {
                "id": f"xhs-live-{index}",
                "label": tags[0] if tags else f"趋势款 {index}",
                "merchantTitle": merchant_title,
                "title": item.get("title", ""),
                "img": image_mapping.get(image_path, fallback_trend_image(index)),
                "likes": item.get("likes", 0),
                "publishedAt": as_date(item.get("published_at")),
                "daysOld": item.get("days_old"),
                "tags": tags,
                "signal": signal,
                "action": action,
                "url": item.get("url", ""),
            }
        )

    style_trends = []
    for item in summary.get("styles", []):
        style_trends.append(
            {
                "style": item.get("style", "趋势款"),
                "status": item.get("status", "热度稳定").replace("稳定观察", "热度稳定"),
                "statusType": item.get("status_type", "stable"),
                "growth": item.get("growth", 0),
                "score90d": item.get("score_90d", 0),
                "score30d": item.get("score_30d", 0),
                "count": item.get("count", 0),
            }
        )

    return {
        "batch": {
            "runId": hot.get("run_id", run.name),
            "keyword": hot.get("keyword", "美甲"),
            "recentDays": hot.get("recent_days", 30),
            "analysisDays": summary.get("analysis_days", 90),
            "count": len(trends),
            "generatedAt": summary.get("generated_at") or hot.get("generated_at"),
            "hotKeywords": [item.get("style") for item in style_trends[:5] if item.get("style")],
        },
        "trends": trends,
        "styleTrends": style_trends,
    }


def run_crawler() -> None:
    global sync_process
    set_state(
        status="syncing",
        progress=8,
        step="正在启动小红书爬虫",
        startedAt=datetime.now().isoformat(timespec="seconds"),
        finishedAt=None,
        error=None,
        logs=[],
    )
    try:
        sync_process = subprocess.Popen(
            [sys.executable, str(CRAWLER)],
            cwd=str(XHS_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert sync_process.stdout is not None
        for line in sync_process.stdout:
            append_log(line)
        code = sync_process.wait()
        if code != 0:
            with sync_lock:
                logs_text = "\n".join(sync_state.get("logs", []))
            error = "浏览器启动被系统拦截，请在外部终端运行 python xhs/main.py" if "spawn EPERM" in logs_text else f"爬虫退出码 {code}"
            set_state(status="error", progress=100, step="爬取失败", error=error)
            return
        payload = build_frontend_payload()
        set_state(
            status="done",
            progress=100,
            step="同步完成，趋势数据已刷新",
            finishedAt=datetime.now().isoformat(timespec="seconds"),
            runId=payload["batch"]["runId"],
            error=None,
        )
    except Exception as exc:
        set_state(status="error", progress=100, step="同步失败", error=str(exc))
    finally:
        sync_process = None


def start_sync() -> dict[str, Any]:
    with sync_lock:
        running = sync_state["status"] == "syncing"
    if not running:
        threading.Thread(target=run_crawler, daemon=True).start()
    return {"ok": True, "status": get_sync_status()}
