from __future__ import annotations

import base64
import http.client
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .prompts.nail_bti_prompt import NAIL_BTI_ANALYSIS_PROMPT
    from .prompts.nail_style_tryon_analysis_prompt import NAIL_STYLE_TRYON_ANALYSIS_PROMPT
except ImportError:
    from prompts.nail_bti_prompt import NAIL_BTI_ANALYSIS_PROMPT
    from prompts.nail_style_tryon_analysis_prompt import NAIL_STYLE_TRYON_ANALYSIS_PROMPT


DEFAULT_DOUBAO_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_MODEL = "doubao-seed-2-0-lite-260215"
DEFAULT_DOUBAO_API_TYPE = "responses"
PLACEHOLDER_VALUES = {"", "你的豆包API_KEY", "your-api-key", "YOUR_API_KEY", "用户自己的豆包API_KEY"}

ALLOWED_VALUES = {
    "white_axis": {"soft_white", "contrast_white"},
    "shape_axis": {"natural_shape", "elongated_shape"},
    "design_axis": {"clean_design", "rich_design"},
    "vibe_axis": {"soft_vibe", "strong_vibe"},
}
AXIS_FIELDS = tuple(ALLOWED_VALUES.keys())
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


def analyze_hand_image(image_bytes: bytes, content_type: str) -> dict[str, Any]:
    config = get_doubao_config()

    if not config["configured"]:
        raise RuntimeError("Doubao hand analysis is not configured")

    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{content_type};base64,{image_base64}"
    endpoint, payload = build_hand_analysis_request(config, data_url)

    response = post_json(endpoint, payload, config["api_key"], timeout=config["timeout"])
    content = extract_message_content(response)
    parsed = parse_json_content(content)
    return normalize_analysis(parsed)


def analyze_nail_style_for_tryon(
    style_image_data_url: str,
    bti_result: dict[str, Any] | None = None,
    length_mode: str = "match_reference",
) -> dict[str, Any]:
    config = get_doubao_config()

    if not config["configured"]:
        raise RuntimeError("Doubao nail style prompt generation is not configured")

    endpoint, payload = build_nail_style_tryon_request(config, style_image_data_url, bti_result, length_mode)
    response = post_json(endpoint, payload, config["api_key"], timeout=config["timeout"])
    content = extract_message_content(response)
    parsed = parse_json_content(content)
    return normalize_nail_tryon_prompt_plan(parsed)


def get_doubao_status() -> dict[str, Any]:
    config = get_doubao_config()
    return {
        "configured": config["configured"],
        "hasApiKey": bool(config["api_key"]),
        "apiBase": config["api_base"],
        "apiType": config["api_type"],
        "endpoint": resolve_model_endpoint(config),
        "model": config["model"],
        "timeout": config["timeout"],
        "mode": "doubao" if config["configured"] else "not-configured",
    }


def get_doubao_config() -> dict[str, Any]:
    load_env()
    api_key = first_env("ARK_API_KEY", "DOUBAO_API_KEY")
    api_base = first_env("DOUBAO_API_BASE", "ARK_API_BASE") or DEFAULT_DOUBAO_API_BASE
    model = first_env("DOUBAO_MODEL", "ARK_MODEL") or DEFAULT_DOUBAO_MODEL
    api_type = normalize_api_type(first_env("DOUBAO_API_TYPE", "ARK_API_TYPE") or DEFAULT_DOUBAO_API_TYPE)
    return {
        "api_key": api_key,
        "api_base": api_base,
        "model": model,
        "api_type": api_type,
        "timeout": normalize_timeout(first_env("DOUBAO_TIMEOUT", "ARK_TIMEOUT") or "120"),
        "configured": bool(api_key and api_base and model),
    }


def first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value and value not in PLACEHOLDER_VALUES:
            return value
    return ""


def load_env() -> None:
    for path in (Path(__file__).with_name(".env"), Path.cwd() / ".env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def resolve_chat_endpoint(api_base: str) -> str:
    trimmed = api_base.rstrip("/")
    if trimmed.endswith("/chat/completions") or trimmed.endswith("/completions"):
        return trimmed
    if trimmed.endswith("/responses"):
        return trimmed.removesuffix("/responses") + "/chat/completions"
    return f"{trimmed}/chat/completions"


def resolve_responses_endpoint(api_base: str) -> str:
    trimmed = api_base.rstrip("/")
    if trimmed.endswith("/responses"):
        return trimmed
    if trimmed.endswith("/chat/completions"):
        return trimmed.removesuffix("/chat/completions") + "/responses"
    if trimmed.endswith("/completions"):
        return trimmed.removesuffix("/completions") + "/responses"
    return f"{trimmed}/responses"


def resolve_model_endpoint(config: dict[str, Any]) -> str:
    if config["api_type"] == "responses":
        return resolve_responses_endpoint(config["api_base"])
    return resolve_chat_endpoint(config["api_base"])


def normalize_api_type(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"chat", "responses"} else DEFAULT_DOUBAO_API_TYPE


def normalize_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return 120
    return max(40, min(timeout, 180))


def build_hand_analysis_request(config: dict[str, Any], data_url: str) -> tuple[str, dict[str, Any]]:
    if config["api_type"] == "responses":
        return (
            resolve_responses_endpoint(config["api_base"]),
            {
                "model": config["model"],
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": data_url, "detail": "high"},
                            {"type": "input_text", "text": NAIL_BTI_ANALYSIS_PROMPT},
                        ],
                    }
                ],
                "text": {"format": {"type": "json_object"}},
                "temperature": 0.2,
            },
        )

    return (
        resolve_chat_endpoint(config["api_base"]),
        {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": NAIL_BTI_ANALYSIS_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        },
    )


def build_nail_style_tryon_request(
    config: dict[str, Any],
    style_image_data_url: str,
    bti_result: dict[str, Any] | None,
    length_mode: str,
) -> tuple[str, dict[str, Any]]:
    context_parts = [
        NAIL_STYLE_TRYON_ANALYSIS_PROMPT,
        f"\n\nSecond-step length_mode: {length_mode or 'match_reference'}",
    ]
    if isinstance(bti_result, dict) and bti_result:
        context_parts.append(
            "\n\nUser Nail-BTI context, for prompt wording only. "
            "Do not let this override the target design image:\n"
            + json.dumps(bti_result, ensure_ascii=False)
        )
    prompt_text = "\n".join(context_parts)

    if config["api_type"] == "responses":
        return (
            resolve_responses_endpoint(config["api_base"]),
            {
                "model": config["model"],
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": style_image_data_url, "detail": "high"},
                            {"type": "input_text", "text": prompt_text},
                        ],
                    }
                ],
                "text": {"format": {"type": "json_object"}},
                "temperature": 0.1,
            },
        )

    return (
        resolve_chat_endpoint(config["api_base"]),
        {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": style_image_data_url}},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        },
    )


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float | None = 40) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False)
    try:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{error.code} {body}") from error
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError):
        curl_timeout = None if timeout is None else max(timeout, 90)
        return post_json_with_curl(url, body, api_key, timeout=curl_timeout)


def post_json_with_curl(url: str, body: str, api_key: str, timeout: float | None = 90) -> dict[str, Any]:
    payload_path = ""
    config_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as payload_file:
            payload_file.write(body)
            payload_path = payload_file.name

        curl_config = "\n".join(
            [
                'request = "POST"',
                'silent',
                'show-error',
                'fail-with-body',
                f'url = "{escape_curl_config(url)}"',
                f'header = "Authorization: Bearer {escape_curl_config(api_key)}"',
                'header = "Content-Type: application/json"',
                f'data-binary = "@{escape_curl_config(payload_path)}"',
            ]
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
            config_file.write(curl_config)
            config_path = config_file.name

        result = subprocess.run(
            ["curl", "-K", config_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            error_parts = [part for part in (result.stderr.strip(), result.stdout.strip()) if part]
            raise RuntimeError("\n".join(error_parts) or f"curl exited {result.returncode}")
        return json.loads(result.stdout)
    finally:
        for path in (payload_path, config_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def escape_curl_config(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def extract_message_content(response: dict[str, Any]) -> str:
    if isinstance(response.get("error"), dict):
        error = response["error"]
        raise RuntimeError(json.dumps(error, ensure_ascii=False))
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    output = response.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if isinstance(content_item, dict) and isinstance(content_item.get("text"), str):
                    text_parts.append(content_item["text"])
        if text_parts:
            return "\n".join(text_parts)
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            return "\n".join(text_parts)
    if isinstance(response.get("content"), str):
        return response["content"]
    return json.dumps(response, ensure_ascii=False)


def parse_json_content(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if match:
            cleaned = match.group(0)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Doubao response JSON is not an object")
    return parsed


def normalize_analysis(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Doubao analysis response must be a JSON object")

    normalized: dict[str, Any] = {}
    for key, allowed in ALLOWED_VALUES.items():
        raw = str(value.get(key, "")).strip().lower()
        if raw not in allowed:
            raise ValueError(f"Invalid or missing {key}: {raw or '<empty>'}")
        normalized[key] = raw

    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("Invalid or missing evidence")
    normalized["evidence"] = {key: normalize_evidence(evidence.get(key), key) for key in AXIS_FIELDS}

    confidence = value.get("confidence")
    if not isinstance(confidence, dict):
        raise ValueError("Invalid or missing confidence")
    normalized["confidence"] = {key: normalize_confidence(confidence.get(key), key) for key in AXIS_FIELDS}
    normalized["white_axis"] = normalize_white_axis(normalized, normalized["evidence"].get("white_axis", ""))
    return normalized


def normalize_nail_tryon_prompt_plan(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Doubao nail try-on prompt plan must be a JSON object")
    if str(value.get("schema_version") or "").strip() != "nail_tryon_prompt_plan_v1":
        raise ValueError("Invalid or missing schema_version for nail try-on prompt plan")

    tryon_prompt = normalize_required_text(value.get("tryon_prompt"), "tryon_prompt", min_length=80, max_length=5000)
    negative_prompt = normalize_required_text(value.get("negative_prompt"), "negative_prompt", min_length=40, max_length=3000)

    style_spec = value.get("style_spec")
    if not isinstance(style_spec, dict):
        raise ValueError("Invalid or missing style_spec")

    finger_designs = value.get("finger_designs")
    if not isinstance(finger_designs, dict):
        raise ValueError("Invalid or missing finger_designs")
    normalized_fingers = {
        finger: normalize_finger_design(finger_designs.get(finger), finger)
        for finger in ("thumb", "index", "middle", "ring", "pinky")
    }

    return {
        "schema_version": "nail_tryon_prompt_plan_v1",
        "overall_style": normalize_text(value.get("overall_style"), max_length=40) or "目标美甲款式",
        "confidence": normalize_optional_float(value.get("confidence"), fallback=0.7),
        "style_spec": {
            "nail_length": normalize_enum(style_spec.get("nail_length"), {"short", "medium", "long", "extra_long", "unknown"}, "unknown"),
            "nail_shape": normalize_enum(style_spec.get("nail_shape"), {"round", "oval", "almond", "square", "squoval", "coffin", "stiletto", "unknown"}, "unknown"),
            "base_colors": normalize_base_colors(style_spec.get("base_colors")),
            "finish": normalize_string_list(style_spec.get("finish"), max_items=8, max_length=30),
            "design_features": normalize_string_list(style_spec.get("design_features"), max_items=12, max_length=40),
            "accent_fingers": [
                item
                for item in normalize_string_list(style_spec.get("accent_fingers"), max_items=5, max_length=12)
                if item in {"thumb", "index", "middle", "ring", "pinky"}
            ],
        },
        "finger_designs": normalized_fingers,
        "decoration_locks": normalize_decoration_locks(value.get("decoration_locks")),
        "transfer_priority": normalize_string_list(value.get("transfer_priority"), max_items=6, max_length=80),
        "tryon_prompt": tryon_prompt,
        "negative_prompt": negative_prompt,
        "quality_checks": normalize_string_list(value.get("quality_checks"), max_items=8, max_length=80),
    }


def normalize_required_text(value: Any, key: str, min_length: int, max_length: int) -> str:
    text = normalize_text(value, max_length=max_length)
    if len(text) < min_length:
        raise ValueError(f"Invalid or too short {key}")
    return text


def normalize_text(value: Any, max_length: int) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:max_length]


def normalize_optional_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(number, 1.0))


def normalize_enum(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def normalize_string_list(value: Any, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = normalize_text(item, max_length=max_length)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def normalize_base_colors(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        name = normalize_text(item.get("name"), max_length=24) or "unknown"
        result.append(
            {
                "name": name,
                "tone": normalize_enum(item.get("tone"), {"warm", "cool", "neutral", "unknown"}, "unknown"),
                "opacity": normalize_enum(item.get("opacity"), {"sheer", "translucent", "opaque", "unknown"}, "unknown"),
                "coverage": normalize_enum(item.get("coverage"), {"all", "most", "accent", "tip", "gradient", "unknown"}, "unknown"),
            }
        )
    return result


def normalize_finger_design(value: Any, finger: str) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "visibility": normalize_enum(source.get("visibility"), {"visible", "partial", "not_visible"}, "not_visible"),
        "base_color": normalize_text(source.get("base_color"), max_length=80) or "unknown",
        "tip_style": normalize_text(source.get("tip_style"), max_length=80) or "unknown",
        "pattern": normalize_text(source.get("pattern"), max_length=120) or "unknown",
        "finish": normalize_string_list(source.get("finish"), max_items=8, max_length=30),
        "decorations": normalize_decorations(source.get("decorations")),
        "notes": normalize_text(source.get("notes"), max_length=80),
        "finger": finger,
    }


def normalize_decorations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "type": normalize_enum(
                    item.get("type"),
                    {"rhinestone", "pearl", "glitter", "bow", "metal_charm", "sticker", "painted_detail", "none", "unknown"},
                    "unknown",
                ),
                "count": normalize_text(item.get("count"), max_length=20) or "unknown",
                "size": normalize_enum(item.get("size"), {"tiny", "small", "medium", "large", "unknown"}, "unknown"),
                "color": normalize_text(item.get("color"), max_length=40) or "unknown",
                "position": normalize_text(item.get("position"), max_length=80) or "unknown",
                "must_keep": bool(item.get("must_keep", True)),
            }
        )
    return result


def normalize_decoration_locks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "finger": normalize_enum(item.get("finger"), {"thumb", "index", "middle", "ring", "pinky", "all", "unknown"}, "unknown"),
                "type": normalize_enum(
                    item.get("type"),
                    {"rhinestone", "pearl", "glitter", "bow", "metal_charm", "sticker", "painted_detail", "unknown"},
                    "unknown",
                ),
                "count": normalize_text(item.get("count"), max_length=20) or "unknown",
                "size": normalize_enum(item.get("size"), {"tiny", "small", "medium", "large", "unknown"}, "unknown"),
                "position": normalize_text(item.get("position"), max_length=80) or "unknown",
                "must_keep": bool(item.get("must_keep", True)),
            }
        )
    return result


def normalize_white_axis(raw_axes: dict[str, Any], evidence_text: str = "") -> str:
    text = json.dumps(raw_axes, ensure_ascii=False) + str(evidence_text)
    contrast_score = sum(1 for keyword in CONTRAST_WHITE_KEYWORDS if keyword in text)
    soft_score = sum(1 for keyword in SOFT_WHITE_KEYWORDS if keyword in text)

    if contrast_score >= soft_score + 1:
        return "contrast_white"
    if soft_score >= contrast_score + 1:
        return "soft_white"

    raw_white_axis = str(raw_axes.get("white_axis", "")).strip().lower()
    return raw_white_axis if raw_white_axis in ALLOWED_VALUES["white_axis"] else "soft_white"


def normalize_evidence(value: Any, key: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"Invalid or missing evidence.{key}")
    return text[:80]


def normalize_confidence(value: Any, key: str) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid or missing confidence.{key}") from None
    if confidence < 0 or confidence > 1:
        raise ValueError(f"Invalid confidence.{key}: {confidence}")
    return confidence
