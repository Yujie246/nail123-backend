from __future__ import annotations

import base64
import json
import mimetypes
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "backend" / "data" / "nail_tryon_prompt_plans"
INDEX_PATH = OUTPUT_DIR / "index.json"
APP_JS_PATH = PROJECT_ROOT / "Nail-main" / "app.js"
HAND_STYLE_LENGTH_MODE = "match_reference"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.doubao_client import analyze_nail_style_for_tryon  # noqa: E402
from backend.tryon_optimizer import get_nail_tryon_v2_config, prepare_tryon_input_image, public_image_prepare_info  # noqa: E402


def parse_styles() -> list[dict[str, Any]]:
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*\[([^\]]*)\]\s*\]'
    )
    styles: list[dict[str, Any]] = []
    for match in pattern.finditer(app_js):
        style_id, name, image, reason, raw_tags = match.groups()
        tags = re.findall(r'"([^"]+)"', raw_tags)
        styles.append(
            {
                "id": style_id,
                "name": name,
                "image": image,
                "reason": reason,
                "tags": tags,
                "path": str(resolve_style_path(image)),
            }
        )
    return styles


def resolve_style_path(image: str) -> Path:
    candidates: list[Path] = []
    if image.startswith("/"):
        relative = image.lstrip("/")
        candidates.append(PROJECT_ROOT / "public" / relative)
        candidates.append(PROJECT_ROOT / "Nail-main" / "public" / relative)
    else:
        candidates.append(PROJECT_ROOT / image)
        candidates.append(PROJECT_ROOT / "Nail-main" / image)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Style image not found: {image}")


def image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def style_output_path(index: int, style: dict[str, Any]) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(style["id"])).strip("-")
    return OUTPUT_DIR / f"{index:02d}-{safe_id}.json"


def build_bti_context(style: dict[str, Any]) -> dict[str, Any]:
    tags = style.get("tags") or []
    return {
        "code": "style-prompt-precompute",
        "archetype": "款式提示词预生成",
        "axes": {
            "white_axis": "contrast_white",
            "shape_axis": "natural_shape",
            "design_axis": "rich_design"
            if any(re.search(r"钻|宝石|蝴蝶结|格纹|豹纹|涂鸦|派对|花朵|镜面", tag) for tag in tags)
            else "clean_design",
        },
        "styleTags": tags,
        "styleName": style.get("name"),
        "styleReason": style.get("reason"),
    }


def generate_plan_for_style(index: int, style: dict[str, Any], max_input_size: int, force: bool) -> dict[str, Any]:
    output_path = style_output_path(index, style)
    if output_path.exists() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        return {**existing, "skipped": True}

    image_data_url = image_to_data_url(Path(style["path"]))
    prepared = prepare_tryon_input_image(image_data_url, max_input_size, "style")
    prepared_data_url = str(prepared.get("data_url") or image_data_url)
    bti_context = build_bti_context(style)

    started = time.monotonic()
    last_error = ""
    for attempt in range(1, 4):
        try:
            plan = analyze_nail_style_for_tryon(prepared_data_url, bti_context, HAND_STYLE_LENGTH_MODE)
            record = {
                "id": style["id"],
                "name": style["name"],
                "image": style["image"],
                "imagePath": style["path"],
                "reason": style.get("reason", ""),
                "tags": style.get("tags", []),
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "latencyMs": int((time.monotonic() - started) * 1000),
                "attempts": attempt,
                "lengthMode": HAND_STYLE_LENGTH_MODE,
                "imagePrepare": public_image_prepare_info(prepared),
                "promptPlan": plan,
                "skipped": False,
            }
            output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
        except Exception as error:
            last_error = str(error)
            if attempt < 3:
                time.sleep(4 * attempt)

    record = {
        "id": style["id"],
        "name": style["name"],
        "image": style["image"],
        "imagePath": style["path"],
        "reason": style.get("reason", ""),
        "tags": style.get("tags", []),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "latencyMs": int((time.monotonic() - started) * 1000),
        "attempts": 3,
        "lengthMode": HAND_STYLE_LENGTH_MODE,
        "imagePrepare": public_image_prepare_info(prepared),
        "error": last_error[:1000],
        "skipped": False,
    }
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def write_index(records: list[dict[str, Any]]) -> None:
    prompt_plans: dict[str, Any] = {}
    summary = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "total": len(records),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "styles": [],
    }
    for index, record in enumerate(records, start=1):
        output_path = style_output_path(index, record)
        has_plan = isinstance(record.get("promptPlan"), dict)
        if has_plan:
            summary["success"] += 1
            prompt_plans[record["id"]] = record["promptPlan"]
        elif record.get("skipped"):
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
        summary["styles"].append(
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "path": str(output_path),
                "success": has_plan,
                "skipped": bool(record.get("skipped")),
                "latencyMs": record.get("latencyMs"),
                "overallStyle": (record.get("promptPlan") or {}).get("overall_style")
                if isinstance(record.get("promptPlan"), dict)
                else "",
                "error": record.get("error", ""),
            }
        )
    INDEX_PATH.write_text(
        json.dumps(
            {
                "schemaVersion": "nail_tryon_prompt_plan_index_v1",
                "summary": summary,
                "promptPlans": prompt_plans,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    force = "--force" in sys.argv
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    styles = parse_styles()
    config = get_nail_tryon_v2_config()
    print(f"PROMPT_PLAN_BATCH_START styles={len(styles)} output={OUTPUT_DIR} force={force}", flush=True)
    records: list[dict[str, Any]] = []
    for index, style in enumerate(styles, start=1):
        print(f"PROMPT_PLAN_STYLE_START {index}/{len(styles)} {style['id']} {style['name']}", flush=True)
        record = generate_plan_for_style(index, style, int(config["max_input_size"]), force)
        records.append(record)
        write_index(records)
        plan = record.get("promptPlan") if isinstance(record.get("promptPlan"), dict) else {}
        print(
            " ".join(
                [
                    f"PROMPT_PLAN_STYLE_DONE {index}/{len(styles)}",
                    str(style["id"]),
                    f"success={bool(plan)}",
                    f"skipped={bool(record.get('skipped'))}",
                    f"latencyMs={record.get('latencyMs', 0)}",
                    f"overallStyle={plan.get('overall_style', '') if plan else ''}",
                    f"error={record.get('error', '')[:120]}" if record.get("error") else "",
                ]
            ).strip(),
            flush=True,
        )
    write_index(records)
    ok = sum(1 for record in records if isinstance(record.get("promptPlan"), dict))
    failed = sum(1 for record in records if record.get("error"))
    print(f"PROMPT_PLAN_BATCH_DONE total={len(records)} success={ok} failed={failed} index={INDEX_PATH}", flush=True)


if __name__ == "__main__":
    main()
