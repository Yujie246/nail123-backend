from __future__ import annotations

import json
from typing import Any


NAIL_BTI_MAP = {
    "WNLS": {
        "archetype": "DREAMER",
        "chineseName": "下次一定换风格体",
        "comment": "连续三年说尝试新风格，连续三年选奶茶裸粉。",
    },
    "WNLA": {
        "archetype": "GHOSTER",
        "chineseName": "老板看不出来体",
        "comment": "做了等于没做，但自己爽了一个月。",
    },
    "WNRS": {
        "archetype": "PLANNER",
        "chineseName": "甲油胶理财师",
        "comment": "做一次美甲，要算30天平均成本。",
    },
    "WNRA": {
        "archetype": "SOCIALITE",
        "chineseName": "电子富家千金",
        "comment": "存款三位数，审美八位数。",
    },
    "WELS": {
        "archetype": "MOONLIGHTER",
        "chineseName": "赛博白月光",
        "comment": "看起来什么都没做，实际最贵。",
    },
    "WELA": {
        "archetype": "EXPLORER",
        "chineseName": "小众赛道冠军",
        "comment": "你喜欢的款式，三个月后才会变成爆款。",
    },
    "WERS": {
        "archetype": "REFINER",
        "chineseName": "高冷但想显白体",
        "comment": "嘴上说无所谓，第一句还是“显白吗？”",
    },
    "WERA": {
        "archetype": "REBEL",
        "chineseName": "审美危险分子",
        "comment": "别人怕撞款，你怕撞审美。",
    },
    "CNLS": {
        "archetype": "SHARER",
        "chineseName": "朋友圈伸手党",
        "comment": "做完美甲第一件事不是照镜子，而是找光。",
    },
    "CNLA": {
        "archetype": "OBSERVER",
        "chineseName": "社恐出片体",
        "comment": "不发自拍，但能发十张手照。",
    },
    "CNRS": {
        "archetype": "RESTARTER",
        "chineseName": "做甲如换命体",
        "comment": "做完美甲当天，人生重新开始。",
    },
    "CNRA": {
        "archetype": "MAXIMIZER",
        "chineseName": "这次真不加钻体",
        "comment": "每次说简单一点，最后还是加满。",
    },
    "CELS": {
        "archetype": "SELECTOR",
        "chineseName": "色卡纠结症",
        "comment": "选色半小时，上手两分钟。",
    },
    "CELA": {
        "archetype": "COLLECTOR",
        "chineseName": "参考图诈骗受害者",
        "comment": "收藏夹800张图，每次做出来都是第801种。",
    },
    "CERS": {
        "archetype": "ORIGINAL",
        "chineseName": "撞款会死党",
        "comment": "最怕的不是丑，是和同事一样。",
    },
    "CERA": {
        "archetype": "VIPER",
        "chineseName": "美甲店VIP候选人",
        "comment": "你不是来做美甲，你是来巡视会员权益。",
    },
}

ALLOWED_AXES = {
    "white_axis": {"soft_white", "contrast_white"},
    "shape_axis": {"natural_shape", "elongated_shape"},
    "design_axis": {"clean_design", "rich_design"},
    "vibe_axis": {"soft_vibe", "strong_vibe"},
}
CONTRAST_WHITE_KEYWORDS = (
    "冷白",
    "偏冷",
    "中性白",
    "肤色较白",
    "冰透",
    "银闪",
    "珍珠白",
    "冷灰",
    "冰蓝",
    "冷亮",
    "高亮",
    "清冷",
    "清透",
    "通透",
    "高光泽",
    "高级感",
    "冷调提亮",
)
SOFT_WHITE_KEYWORDS = (
    "偏暖",
    "偏黄",
    "暖黄",
    "橄榄",
    "橄榄调",
    "暖调",
    "奶茶",
    "豆沙",
    "裸粉",
    "焦糖",
    "杏仁",
    "蜜桃",
    "低饱和",
    "柔和",
    "温柔",
)


def build_bti_code(axes: dict[str, Any]) -> str:
    normalized = {}
    for key, allowed in ALLOWED_AXES.items():
        value = str(axes.get(key, "")).strip().lower()
        if value not in allowed:
            raise ValueError(f"Invalid or missing {key}: {value or '<empty>'}")
        normalized[key] = value

    evidence = axes.get("evidence") if isinstance(axes.get("evidence"), dict) else {}
    normalized["white_axis"] = normalize_white_axis(normalized, str(evidence.get("white_axis", "")))
    white = "W" if normalized["white_axis"] == "soft_white" else "C"
    shape = "N" if normalized["shape_axis"] == "natural_shape" else "E"
    design = "L" if normalized["design_axis"] == "clean_design" else "R"
    vibe = "S" if normalized["vibe_axis"] == "soft_vibe" else "A"
    return f"{white}{shape}{design}{vibe}"


def normalize_white_axis(raw_axes: dict[str, Any], evidence_text: str = "") -> str:
    text = json.dumps(raw_axes, ensure_ascii=False) + evidence_text
    contrast_score = sum(1 for keyword in CONTRAST_WHITE_KEYWORDS if keyword in text)
    soft_score = sum(1 for keyword in SOFT_WHITE_KEYWORDS if keyword in text)

    if contrast_score >= soft_score + 1:
        return "contrast_white"
    if soft_score >= contrast_score + 1:
        return "soft_white"
    return str(raw_axes.get("white_axis", "soft_white")).strip().lower() or "soft_white"


def map_hand_analysis_to_bti(analysis: dict[str, Any]) -> dict[str, Any]:
    code = build_bti_code(analysis)
    persona = NAIL_BTI_MAP[code]
    archetype = persona["archetype"]
    axes = {
        "white": "暖柔显白型" if analysis.get("white_axis") == "soft_white" else "冷亮提白型",
        "shape": "自然修饰型" if analysis.get("shape_axis") == "natural_shape" else "延伸修手型",
        "design": "简洁设计型" if analysis.get("design_axis") == "clean_design" else "丰富设计型",
        "vibe": "温柔通勤型" if analysis.get("vibe_axis") == "soft_vibe" else "强风格气场型",
    }
    return {
        "code": code,
        "archetype": archetype,
        "chineseName": persona["chineseName"],
        "comment": persona["comment"],
        "personaAnalysis": build_persona_analysis(analysis),
        "avatarUrl": f"/personas/{archetype.lower()}.png",
        "stats": build_stats(analysis, axes),
        "recommendedStyleIds": [],
        "avoidStyles": [],
        "axes": axes,
        "axisAnalysis": analysis,
    }


def build_persona_analysis(analysis: dict[str, Any]) -> str:
    evidence = analysis.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    values = [str(evidence.get(key, "")).strip() for key in ALLOWED_AXES]
    return " ".join(value for value in values if value)


def build_stats(analysis: dict[str, Any], axes: dict[str, str]) -> list[dict[str, Any]]:
    confidence = analysis.get("confidence") if isinstance(analysis.get("confidence"), dict) else {}
    evidence = analysis.get("evidence") if isinstance(analysis.get("evidence"), dict) else {}
    rows = [
        ("显白指数", "white", "white_axis"),
        ("修手指数", "shape", "shape_axis"),
        ("甲面承载指数", "design", "design_axis"),
        ("线条气质指数", "vibe", "vibe_axis"),
    ]
    stats = []
    for label, axis_key, raw_key in rows:
        value = int(max(60, min(99, round(float(confidence.get(raw_key, 0.8)) * 100))))
        stats.append(
            {
                "label": label,
                "value": value,
                "stars": max(1, min(5, round(value / 20))),
                "result": axes[axis_key],
                "evidence": str(evidence.get(raw_key, "")).strip(),
            }
        )
    return stats
