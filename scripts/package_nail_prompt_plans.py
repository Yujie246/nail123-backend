from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = PROJECT_ROOT / "backend" / "data" / "nail_tryon_prompt_plans"
BUNDLE_PATH = PROMPT_DIR / "ui_prompt_plan_bundle.json"


def main() -> None:
    files = sorted(PROMPT_DIR.glob("[0-9][0-9]-*.json"))
    styles = []
    by_id = {}
    by_name = {}

    for order, path in enumerate(files, start=1):
        record = json.loads(path.read_text(encoding="utf-8"))
        prompt_plan = record.get("promptPlan")
        if not isinstance(prompt_plan, dict):
            continue
        style = {
            "order": order,
            "uiId": record["id"],
            "uiName": record["name"],
            "uiImage": record["image"],
            "uiTags": record.get("tags", []),
            "sourceFile": str(path.relative_to(PROJECT_ROOT)),
            "overallStyle": prompt_plan.get("overall_style", ""),
            "schemaVersion": prompt_plan.get("schema_version", ""),
            "promptPlan": prompt_plan,
        }
        styles.append(style)
        by_id[style["uiId"]] = style
        by_name[style["uiName"]] = style

    bundle = {
        "schemaVersion": "nail_tryon_ui_prompt_plan_bundle_v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "source": "backend/data/nail_tryon_prompt_plans",
        "total": len(styles),
        "styles": styles,
        "byId": by_id,
        "byName": by_name,
    }
    BUNDLE_PATH.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"packaged={BUNDLE_PATH} total={len(styles)}")


if __name__ == "__main__":
    main()
