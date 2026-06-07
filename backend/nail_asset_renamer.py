from __future__ import annotations

import argparse
from pathlib import Path


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

STYLE_RULES = [
    ("cat_eye", "冰透猫眼", "cat_eye", "通勤显白", ["显白", "猫眼", "通勤", "出片"], ["#d9f7ff", "#f5f3ff", "#8ecae6"], "Ice", "Bold", "Aura", "Pioneer"),
    ("cat", "冰透猫眼", "cat_eye", "通勤显白", ["显白", "猫眼", "通勤", "出片"], ["#d9f7ff", "#f5f3ff", "#8ecae6"], "Ice", "Bold", "Aura", "Pioneer"),
    ("milk", "奶茶裸粉", "milk_tea", "日常百搭", ["奶茶", "裸粉", "低调", "百搭"], ["#d9b99b", "#f4dfcf", "#b88973"], "Warm", "Classic", "Soft", "Follow"),
    ("pearl", "珍珠法式", "pearl_french", "温柔气质", ["珍珠", "法式", "气质", "通勤"], ["#fff4e6", "#f7d6c1", "#fdfdf6"], "Warm", "Classic", "Soft", "Follow"),
    ("french", "法式", "french", "温柔气质", ["法式", "通勤", "气质"], ["#fff4e6", "#f7d6c1", "#fdfdf6"], "Warm", "Classic", "Soft", "Follow"),
    ("dopamine", "多巴胺渐变", "dopamine_gradient", "社交出片", ["出片", "渐变", "明亮"], ["#ff7096", "#ffd166", "#70d6ff"], "Warm", "Bold", "Aura", "Pioneer"),
    ("black", "暗黑风", "black_style", "先锋酷感", ["暗黑", "酷感", "个性"], ["#14100e", "#5d5660", "#e7ecef"], "Ice", "Bold", "Aura", "Pioneer"),
    ("gold", "轻奢金", "gold_luxury", "轻奢高级", ["金色", "高级", "聚会"], ["#14100e", "#d7b55d", "#fff1c7"], "Warm", "Bold", "Aura", "Follow"),
]

DEFAULT_STYLE = {
    "name": "美甲款式",
    "slug": "style",
    "category": "精选款式",
    "tags": ["精选", "百搭"],
    "palette": ["#f4c9c2", "#fff7f4", "#c56772"],
    "tone": "Warm",
    "style": "Classic",
    "presence": "Soft",
    "trend": "Follow",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename nail assets and generate nailStyles.ts.")
    parser.add_argument("folder", nargs="?", default="public/nails", help="Folder containing nail images.")
    parser.add_argument("--output", default="src/data/nailStyles.ts", help="TypeScript data output path.")
    parser.add_argument("--public-prefix", default="/nails", help="Public URL prefix used by the frontend.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without renaming files.")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise SystemExit(f"Folder not found: {folder}")

    images = sorted(path for path in folder.iterdir() if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file())
    planned = plan_renames(images)

    if args.dry_run:
        for source, target, _style in planned:
            print(f"{source.name} -> {target.name}")
        return

    apply_renames(planned)
    write_styles(Path(args.output), planned, args.public_prefix.rstrip("/"))
    print(f"Generated {args.output} with {len(planned)} styles.")


def plan_renames(images: list[Path]):
    planned = []
    for index, image in enumerate(images, start=1):
        style = detect_style(image.name)
        target = image.with_name(f"nail_{index:03d}_{style['slug']}{image.suffix.lower()}")
        planned.append((image, target, style))
    return planned


def apply_renames(planned) -> None:
    temp_paths = []
    for index, (source, _target, _style) in enumerate(planned):
        temp = source.with_name(f".nail_asset_tmp_{index:03d}{source.suffix.lower()}")
        if source != temp:
            source.rename(temp)
        temp_paths.append(temp)

    for temp, (_source, target, _style) in zip(temp_paths, planned):
        temp.rename(target)


def detect_style(filename: str) -> dict:
    lower = filename.lower().replace("-", "_").replace(" ", "_")
    for keyword, name, slug, category, tags, palette, tone, style, presence, trend in STYLE_RULES:
        if keyword in lower:
            return {
                "name": name,
                "slug": slug,
                "category": category,
                "tags": tags,
                "palette": palette,
                "tone": tone,
                "style": style,
                "presence": presence,
                "trend": trend,
            }
    return dict(DEFAULT_STYLE)


def write_styles(output: Path, planned, public_prefix: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for _source, target, style in planned:
        item_id = target.stem
        items.append(
            f"""  {{
    id: "{item_id}",
    name: "{style['name']}",
    image: "{public_prefix}/{target.name}",
    category: "{style['category']}",
    description: "{style['name']}适合作为 Nail-BTI 推荐款式。",
    palette: {to_ts_array(style['palette'])},
    tags: {to_ts_array(style['tags'])},
    tone: "{style['tone']}",
    style: "{style['style']}",
    presence: "{style['presence']}",
    trend: "{style['trend']}",
  }}"""
        )

    content = """import type { NailStyle } from "../lib/types";

export const nailStyles: NailStyle[] = [
""" + ",\n".join(items) + "\n];\n"
    output.write_text(content, encoding="utf-8")


def to_ts_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


if __name__ == "__main__":
    main()
