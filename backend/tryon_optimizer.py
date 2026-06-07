from __future__ import annotations

import json
import base64
import hashlib
import io
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .doubao_client import DEFAULT_DOUBAO_API_BASE, analyze_nail_style_for_tryon, first_env, load_env, post_json
    from .prompts.tryon_refine_prompt import TRYON_REFINE_NEGATIVE_PROMPT, TRYON_REFINE_PROMPT
    from .prompts.nail_tryon_v2_prompt import (
        NAIL_TRYON_V2_CONFIG,
    )
    from .prompts.nail_style_generate_prompt import NAIL_STYLE_GENERATE_PROMPT
    from .prompts.tryon_refine_prompt import (
        NAIL_EDGE_NEGATIVE_PROMPT,
        NAIL_STYLE_TRANSFER_NEGATIVE_PROMPT,
        NAIL_STYLE_TRANSFER_PROMPT,
        NAIL_STYLE_TRANSFER_WITH_DRAFT_PROMPT,
        NAIL_STYLE_TRANSFER_WITH_EDGE_REFINE_PROMPT,
    )
except ImportError:
    from doubao_client import DEFAULT_DOUBAO_API_BASE, analyze_nail_style_for_tryon, first_env, load_env, post_json
    from prompts.tryon_refine_prompt import TRYON_REFINE_NEGATIVE_PROMPT, TRYON_REFINE_PROMPT
    from prompts.nail_tryon_v2_prompt import (
        NAIL_TRYON_V2_CONFIG,
    )
    from prompts.nail_style_generate_prompt import NAIL_STYLE_GENERATE_PROMPT
    from prompts.tryon_refine_prompt import (
        NAIL_EDGE_NEGATIVE_PROMPT,
        NAIL_STYLE_TRANSFER_NEGATIVE_PROMPT,
        NAIL_STYLE_TRANSFER_PROMPT,
        NAIL_STYLE_TRANSFER_WITH_DRAFT_PROMPT,
        NAIL_STYLE_TRANSFER_WITH_EDGE_REFINE_PROMPT,
    )


DEFAULT_SEEDDREAM_API_BASE = f"{DEFAULT_DOUBAO_API_BASE}/images/generations"
DEFAULT_SEEDDREAM_MODEL = "doubao-seedream-5-0-260128"
DEFAULT_APIMART_API_BASE = "https://api.aishuch.com/v1/images/generations"
DEFAULT_APIMART_MODEL = "gpt-image-2-official"
DEFAULT_APIMART_SIZE = "auto"
DEFAULT_APIMART_RESOLUTION = "1k"
DEFAULT_APIMART_TASK_TIMEOUT = 0
DEFAULT_TRYON_SUBMIT_TIMEOUT = 90
DEFAULT_TRYON_TASK_TIMEOUT = 600
APIMART_COMPLETED_STATUSES = {"completed", "succeeded", "success"}
APIMART_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled", "rejected"}
STYLE_PROMPT_PLAN_CACHE: dict[str, dict[str, Any]] = {}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_PROMPT_PLAN_BUNDLE_PATH = PROJECT_ROOT / "backend" / "data" / "nail_tryon_prompt_plans" / "ui_prompt_plan_bundle.json"
UI_PROMPT_PLAN_BUNDLE_CACHE: dict[str, Any] | None = None


def optimize_tryon_image(image_data_url: str) -> str:
    return transfer_nail_style(image_data_url, image_data_url, image_data_url)["final_tryon_image"]


def transfer_nail_style(
    hand_image: str,
    style_image: str,
    draft_tryon_image: str | None = None,
) -> dict[str, str]:
    config = get_seeddream_config()
    fallback_image = draft_tryon_image or hand_image

    if not config["configured"]:
        return transfer_response(fallback_image, draft_tryon_image)

    payload = build_transfer_payload(
        config["model"],
        config["size"],
        config["resolution"],
        hand_image,
        style_image,
        draft_tryon_image,
    )

    try:
        response = post_json(config["api_base"], payload, config["api_key"], timeout=None)
        final_image = wait_for_apimart_image(response, config["api_base"], config["api_key"], DEFAULT_APIMART_TASK_TIMEOUT) or fallback_image
        return transfer_response(final_image, draft_tryon_image)
    except Exception as error:
        print(f"Image generation nail style transfer failed, using draft/original image: {error}")
        return transfer_response(fallback_image, draft_tryon_image)


def generate_nail_tryon_v2(
    hand_image: str,
    style_image: str,
    bti_result: dict[str, Any] | None = None,
    fast_mode: bool | None = None,
    length_mode: str | None = None,
    label_bundle: dict[str, Any] | None = None,
    style_id: str | None = None,
    style_name: str | None = None,
) -> dict[str, Any]:
    config = get_nail_tryon_v2_config()
    started_at = time.monotonic()
    timings: dict[str, int] = {}
    diagnostics: dict[str, Any] = {
        "model": config["model"],
        "max_input_size": config["max_input_size"],
        "submit_timeout_seconds": config["submit_timeout_seconds"],
        "task_timeout_seconds": config["task_timeout_seconds"],
        "style_id": style_id or "",
        "style_name": style_name or "",
    }

    prepare_started = time.monotonic()
    prepared_hand = prepare_tryon_input_image(hand_image, config["max_input_size"], "hand")
    prepared_style = prepare_tryon_input_image(style_image, config["max_input_size"], "style")
    hand_image = prepared_hand["data_url"]
    style_image = prepared_style["data_url"]
    timings["prepare_images_ms"] = elapsed_ms(prepare_started)
    diagnostics["input_images"] = {
        "hand": public_image_prepare_info(prepared_hand),
        "style": public_image_prepare_info(prepared_style),
    }

    if not config["configured"]:
        return v2_failure("AI生成服务未配置", started_at, timings=timings, diagnostics=diagnostics)

    normalized_length_mode = normalize_length_mode(length_mode or config["length_mode"])
    style_prompt_cache_key = build_style_prompt_cache_key(style_image, bti_result, normalized_length_mode)
    diagnostics["doubao_prompt_plan_cache_hit"] = False
    diagnostics["local_prompt_plan_hit"] = False
    diagnostics["style_prompt_plan_source"] = "doubao_live"
    try:
        local_prompt_plan = load_ui_style_prompt_plan(style_id, style_name)
        if local_prompt_plan:
            local_started = time.monotonic()
            style_prompt_plan = dict(local_prompt_plan)
            timings["doubao_prompt_plan_ms"] = elapsed_ms(local_started)
            diagnostics["local_prompt_plan_hit"] = True
            diagnostics["doubao_prompt_plan_cache_hit"] = True
            diagnostics["style_prompt_plan_source"] = "local_ui_bundle"
            if config["style_prompt_cache_enabled"]:
                STYLE_PROMPT_PLAN_CACHE[style_prompt_cache_key] = dict(style_prompt_plan)
                trim_style_prompt_plan_cache()
        elif config["style_prompt_cache_enabled"] and style_prompt_cache_key in STYLE_PROMPT_PLAN_CACHE:
            cache_started = time.monotonic()
            style_prompt_plan = dict(STYLE_PROMPT_PLAN_CACHE[style_prompt_cache_key])
            timings["doubao_prompt_plan_ms"] = elapsed_ms(cache_started)
            diagnostics["doubao_prompt_plan_cache_hit"] = True
            diagnostics["style_prompt_plan_source"] = "memory_cache"
        else:
            doubao_started = time.monotonic()
            style_prompt_plan = analyze_nail_style_for_tryon(style_image, bti_result, normalized_length_mode)
            timings["doubao_prompt_plan_ms"] = elapsed_ms(doubao_started)
            if config["style_prompt_cache_enabled"]:
                STYLE_PROMPT_PLAN_CACHE[style_prompt_cache_key] = dict(style_prompt_plan)
                trim_style_prompt_plan_cache()
    except Exception as error:
        timings["doubao_prompt_plan_ms"] = elapsed_ms(started_at) - sum(timings.values())
        print(f"Doubao nail style prompt generation failed: {error}")
        return v2_failure(
            f"豆包款式提示词生成失败：{format_upstream_error(error)}",
            started_at,
            timings=timings,
            diagnostics=diagnostics,
        )

    upload_started = time.monotonic()
    try:
        uploaded_hand = upload_apimart_image(hand_image, config["api_base"], config["api_key"], "hand")
        uploaded_style = upload_apimart_image(style_image, config["api_base"], config["api_key"], "style")
        hand_image = uploaded_hand["url"]
        style_image = uploaded_style["url"]
        diagnostics["uploaded_images"] = {
            "hand": public_uploaded_image_info(uploaded_hand),
            "style": public_uploaded_image_info(uploaded_style),
        }
        if label_bundle and label_bundle.get("success") and label_bundle.get("combined_labeled_image"):
            label_bundle = dict(label_bundle)
            uploaded_labels = upload_apimart_image(
                str(label_bundle["combined_labeled_image"]),
                config["api_base"],
                config["api_key"],
                "labels",
            )
            label_bundle["combined_labeled_image"] = uploaded_labels["url"]
            diagnostics["uploaded_images"]["labels"] = public_uploaded_image_info(uploaded_labels)
    except Exception as error:
        timings["apimart_upload_images_ms"] = elapsed_ms(upload_started)
        print(f"APIMart image upload failed: {error}")
        return v2_failure(
            f"图片上传失败：{format_upstream_error(error)}",
            started_at,
            timings=timings,
            diagnostics=diagnostics,
            style_prompt_plan=style_prompt_plan,
        )
    timings["apimart_upload_images_ms"] = elapsed_ms(upload_started)

    payload_started = time.monotonic()
    payload = build_tryon_v2_payload(
        config["model"],
        config["size"],
        config["resolution"],
        hand_image,
        style_image,
        bti_result,
        config["fast_mode"] if fast_mode is None else fast_mode,
        normalized_length_mode,
        label_bundle,
        style_prompt_plan,
    )
    timings["build_payload_ms"] = elapsed_ms(payload_started)
    diagnostics["prompt_chars"] = len(str(payload.get("prompt") or ""))
    diagnostics["image_count"] = len(payload.get("image_urls") or [])

    try:
        submit_started = time.monotonic()
        response = post_json(config["api_base"], payload, config["api_key"], timeout=config["submit_timeout_seconds"])
        timings["apimart_submit_ms"] = elapsed_ms(submit_started)
        diagnostics["apimart_submit_has_task_id"] = bool(extract_task_id(response))
        diagnostics["apimart_submit_has_image"] = bool(extract_image(response))

        wait_started = time.monotonic()
        image = wait_for_apimart_image(response, config["api_base"], config["api_key"], config["task_timeout_seconds"])
        timings["apimart_wait_image_ms"] = elapsed_ms(wait_started)
        if not image:
            return v2_failure(
                "AI生成失败：上游没有返回图片或任务ID",
                started_at,
                timings=timings,
                diagnostics=diagnostics,
                style_prompt_plan=style_prompt_plan,
            )
        return {
            "success": True,
            "mode": "ai_generate_v2",
            "image": image,
            "latency_ms": elapsed_ms(started_at),
            "timings": timings,
            "diagnostics": diagnostics,
            "finger_labels": public_label_bundle(label_bundle),
            "style_prompt_plan": public_style_prompt_plan(style_prompt_plan),
        }
    except Exception as error:
        if "apimart_submit_ms" not in timings:
            timings["apimart_submit_ms"] = elapsed_ms(started_at) - sum(timings.values())
        elif "apimart_wait_image_ms" not in timings:
            timings["apimart_wait_image_ms"] = elapsed_ms(started_at) - sum(timings.values())
        print(f"APIMart nail try-on V2 failed: {error}")
        return v2_failure(
            f"AI生成失败：{format_upstream_error(error)}",
            started_at,
            timings=timings,
            diagnostics=diagnostics,
            style_prompt_plan=style_prompt_plan,
        )


def generate_nail_style(
    prompt: str,
    bti_result: dict[str, Any] | None = None,
    hand_image: str | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    config = get_nail_tryon_v2_config()
    style_id = f"ai_style_{int(time.time() * 1000)}"
    style_name = infer_style_name(prompt)
    tags = infer_style_tags(prompt, bti_result)
    description = infer_style_description(prompt, tags)
    bti_fit_reason = infer_bti_fit_reason(bti_result, tags)
    image = "/nails/06_6c857edd85a5fa4bcec59698fe9416cb1913981.png"
    image_source = "local_fallback"
    upstream_error = ""

    if config["configured"]:
        try:
            uploaded_hand_image = ""
            if hand_image:
                uploaded = upload_apimart_image(
                    prepare_tryon_input_image(hand_image, config["max_input_size"], "style_hand")["data_url"],
                    config["api_base"],
                    config["api_key"],
                    "style_hand",
                )
                uploaded_hand_image = uploaded["url"]
            payload = build_style_generate_payload(
                config["model"],
                config["size"],
                config["resolution"],
                prompt,
                bti_result,
                uploaded_hand_image,
            )
            response = post_json(config["api_base"], payload, config["api_key"], timeout=config["submit_timeout_seconds"])
            generated_image = wait_for_apimart_image(response, config["api_base"], config["api_key"], config["task_timeout_seconds"])
            if generated_image:
                image = generated_image
                image_source = "apimart"
        except Exception as error:
            upstream_error = format_upstream_error(error)
            print(f"APIMart nail style generation failed, using mock style image: {error}")

    return {
        "success": True,
        "style": {
            "id": style_id,
            "name": style_name,
            "image": image,
            "tags": tags,
            "description": description,
            "difficulty": "medium",
            "estimatedPrice": "168-228元",
            "btiFitReason": bti_fit_reason,
        },
        "imageSource": image_source,
        "upstreamError": upstream_error,
        "diagnostics": {
            "model": config["model"],
            "configured": config["configured"],
            "submit_timeout_seconds": config["submit_timeout_seconds"],
            "task_timeout_seconds": config["task_timeout_seconds"],
        },
        "latency_ms": elapsed_ms(started_at),
    }


def get_tryon_optimizer_status() -> dict[str, Any]:
    config = get_seeddream_config()
    v2_config = get_nail_tryon_v2_config()
    return {
        "configured": config["configured"],
        "hasApiKey": bool(config["api_key"]),
        "apiBase": config["api_base"],
        "model": config["model"],
        "size": config["size"],
        "resolution": config["resolution"],
        "mode": "legacy-transfer" if config["configured"] else "not-configured",
        "v2": {
            "configured": v2_config["configured"],
            "mode": "ai_generate_v2" if v2_config["configured"] else "not-configured",
            "prompt_source": "doubao_nail_tryon_prompt_plan_v1",
            "apiBase": v2_config["api_base"],
            "model": v2_config["model"],
            "size": v2_config["size"],
            "resolution": v2_config["resolution"],
            "timeout_ms": v2_config["timeout_ms"],
            "submit_timeout_seconds": v2_config["submit_timeout_seconds"],
            "task_timeout_seconds": v2_config["task_timeout_seconds"],
            "fast_mode": v2_config["fast_mode"],
            "max_input_size": v2_config["max_input_size"],
            "style_prompt_cache_enabled": v2_config["style_prompt_cache_enabled"],
            "style_prompt_cache_size": len(STYLE_PROMPT_PLAN_CACHE),
            "ui_prompt_plan_bundle_exists": UI_PROMPT_PLAN_BUNDLE_PATH.exists(),
            "ui_prompt_plan_bundle_count": ui_prompt_plan_bundle_count(),
            "length_mode": v2_config["length_mode"],
        },
    }


def get_seeddream_config() -> dict[str, Any]:
    load_env()
    api_key = first_env("APIMART_API_KEY", "SEEDDREAM_API_KEY", "ARK_API_KEY", "DOUBAO_API_KEY")
    api_base = (
        first_env("APIMART_API_BASE", "SEEDDREAM_API_BASE", "DOUBAO_OPTIMIZE_API_BASE", "DOUBAO_IMAGE_API_BASE")
        or DEFAULT_APIMART_API_BASE
    )
    model = first_env("APIMART_MODEL", "SEEDDREAM_MODEL", "DOUBAO_IMAGE_MODEL") or DEFAULT_APIMART_MODEL
    size = normalize_apimart_size(first_env("APIMART_SIZE", "SEEDDREAM_SIZE", "DOUBAO_IMAGE_SIZE") or DEFAULT_APIMART_SIZE)
    resolution = normalize_apimart_resolution(first_env("APIMART_RESOLUTION", "SEEDDREAM_RESOLUTION") or DEFAULT_APIMART_RESOLUTION)
    return {
        "api_key": api_key,
        "api_base": api_base,
        "model": model,
        "size": size,
        "resolution": resolution,
        "configured": bool(api_key and api_base and model),
    }


def get_nail_tryon_v2_config() -> dict[str, Any]:
    base = get_seeddream_config()
    model = first_env("NAIL_TRYON_V2_MODEL") or base["model"]
    size = normalize_apimart_size(first_env("NAIL_TRYON_V2_SIZE") or DEFAULT_APIMART_SIZE)
    resolution = normalize_apimart_resolution(first_env("NAIL_TRYON_V2_RESOLUTION") or base["resolution"])
    max_input_size = parse_int(first_env("NAIL_TRYON_V2_MAX_INPUT_SIZE") or str(NAIL_TRYON_V2_CONFIG["max_input_size"]), 1024)
    submit_timeout_seconds = parse_int(first_env("NAIL_TRYON_V2_SUBMIT_TIMEOUT") or str(DEFAULT_TRYON_SUBMIT_TIMEOUT), DEFAULT_TRYON_SUBMIT_TIMEOUT)
    task_timeout_seconds = parse_int(
        first_env("NAIL_TRYON_V2_TASK_TIMEOUT", "NAIL_TRYON_V2_TIMEOUT") or str(DEFAULT_TRYON_TASK_TIMEOUT),
        DEFAULT_TRYON_TASK_TIMEOUT,
    )
    fast_mode = normalize_bool(first_env("NAIL_TRYON_V2_FAST_MODE"), bool(NAIL_TRYON_V2_CONFIG["fast_mode"]))
    length_mode = normalize_length_mode(first_env("NAIL_TRYON_V2_LENGTH_MODE") or str(NAIL_TRYON_V2_CONFIG["length_mode"]))
    return {
        **base,
        "model": model,
        "size": size,
        "resolution": resolution,
        "timeout_ms": task_timeout_seconds * 1000,
        "submit_timeout_seconds": max(30, min(submit_timeout_seconds, 300)),
        "task_timeout_seconds": max(60, min(task_timeout_seconds, 1800)),
        "max_input_size": max(512, min(max_input_size, 2048)),
        "fast_mode": fast_mode,
        "style_prompt_cache_enabled": normalize_bool(first_env("NAIL_TRYON_V2_CACHE_STYLE_PROMPT"), True),
        "save_debug_images": normalize_bool(first_env("NAIL_TRYON_V2_SAVE_DEBUG_IMAGES"), bool(NAIL_TRYON_V2_CONFIG["save_debug_images"])),
        "length_mode": length_mode,
    }


def build_transfer_payload(
    model: str,
    size: str,
    resolution: str,
    hand_image: str,
    style_image: str,
    draft_tryon_image: str | None,
) -> dict[str, Any]:
    if draft_tryon_image:
        prompt = "\n\n".join(
            [
                NAIL_STYLE_TRANSFER_WITH_DRAFT_PROMPT,
                NAIL_STYLE_TRANSFER_WITH_EDGE_REFINE_PROMPT,
                f"负向约束：\n{NAIL_STYLE_TRANSFER_NEGATIVE_PROMPT}\n{NAIL_EDGE_NEGATIVE_PROMPT}",
            ]
        )
        images = [hand_image, style_image, draft_tryon_image]
    else:
        prompt = f"{NAIL_STYLE_TRANSFER_PROMPT}\n\n负向约束：\n{NAIL_STYLE_TRANSFER_NEGATIVE_PROMPT}"
        images = [hand_image, style_image]

    return {
        "model": model,
        "prompt": prompt,
        "image_urls": images,
        "n": 1,
        "size": size,
        "resolution": resolution,
    }


def build_tryon_v2_payload(
    model: str,
    size: str,
    resolution: str,
    hand_image: str,
    style_image: str,
    bti_result: dict[str, Any] | None,
    fast_mode: bool,
    length_mode: str,
    label_bundle: dict[str, Any] | None = None,
    style_prompt_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bti_context = ""
    if isinstance(bti_result, dict) and bti_result:
        bti_context = (
            "\n\nThe user's Nail-BTI result may be used only as style-fit context. "
            f"Do not change the user's hand structure:\n{bti_result}"
        )
    fast_context = "\n\nPrioritize a natural, realistic result within a reasonable generation time." if fast_mode else ""
    has_labels = bool(label_bundle and label_bundle.get("success") and label_bundle.get("combined_labeled_image"))
    length_context = f"\n\nlengthMode: {length_mode}."
    label_context = ""
    images = [hand_image, style_image]
    if has_labels:
        mapping = label_bundle.get("mapping") if isinstance(label_bundle, dict) else None
        label_context = (
            "\n\nFinger design mapping table:\n"
            f"{mapping}\n\n"
            "Strictly follow the T/I/M/R/P helper labels in the labeled image. "
            "Do not transfer designs based on visual left-to-right order."
        )
        images.append(str(label_bundle["combined_labeled_image"]))
    if not isinstance(style_prompt_plan, dict):
        raise ValueError("Missing Doubao generated nail try-on prompt plan")
    tryon_prompt = str(style_prompt_plan.get("tryon_prompt") or "").strip()
    negative_prompt = str(style_prompt_plan.get("negative_prompt") or "").strip()
    if not tryon_prompt:
        raise ValueError("Missing Doubao generated tryon_prompt")
    style_plan_context = (
        "\n\nStructured nail style plan generated by Doubao for this exact reference image:\n"
        + json.dumps(public_style_prompt_plan(style_prompt_plan), ensure_ascii=False)
    )
    prompt = "\n\n".join(
        [
            tryon_prompt,
            style_plan_context,
            f"Doubao generated negative prompt:\n{negative_prompt}" if negative_prompt else "",
            length_context,
            label_context,
            bti_context,
            fast_context,
        ]
    ).strip()

    return {
        "model": model,
        "prompt": prompt,
        "image_urls": images,
        "n": 1,
        "size": size,
        "resolution": resolution,
    }


def build_style_generate_payload(
    model: str,
    size: str,
    resolution: str,
    prompt: str,
    bti_result: dict[str, Any] | None,
    hand_image: str | None,
) -> dict[str, Any]:
    bti_context = f"\n\n用户 Nail-BTI 信息：\n{bti_result}" if isinstance(bti_result, dict) and bti_result else ""
    user_context = f"\n\n用户描述：\n{prompt.strip()}"
    final_prompt = f"{NAIL_STYLE_GENERATE_PROMPT}{user_context}{bti_context}".strip()
    payload: dict[str, Any] = {
        "model": model,
        "prompt": final_prompt,
        "n": 1,
        "size": size,
        "resolution": resolution,
    }
    if hand_image:
        payload["image_urls"] = [hand_image]
    return payload


def public_label_bundle(label_bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(label_bundle, dict):
        return None
    return {
        "success": bool(label_bundle.get("success")),
        "error": label_bundle.get("error"),
        "mapping": label_bundle.get("mapping"),
        "hand": label_bundle.get("hand"),
        "style": label_bundle.get("style"),
        "combined_labeled_image": label_bundle.get("combined_labeled_image"),
    }


def prepare_tryon_input_image(image_data_url: str, max_input_size: int, role: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "role": role,
        "data_url": image_data_url,
        "original_bytes": len(image_data_url.encode("utf-8")),
        "prepared_bytes": len(image_data_url.encode("utf-8")),
        "resized": False,
        "reencoded": False,
        "error": "",
    }
    if not image_data_url.startswith("data:image/") or "," not in image_data_url:
        info["error"] = "not_data_url"
        return info

    header, encoded = image_data_url.split(",", 1)
    source_mime = header.split(";", 1)[0].removeprefix("data:") or "image/png"
    try:
        raw = base64.b64decode(encoded, validate=False)
        info["original_bytes"] = len(raw)
        info["prepared_bytes"] = len(raw)
        info["prepared_mime"] = source_mime
    except Exception as error:
        info["error"] = f"decode_failed:{error.__class__.__name__}"
        return info

    try:
        from PIL import Image
    except Exception as error:
        info["error"] = f"pillow_unavailable:{error.__class__.__name__}"
        return info

    try:
        image = Image.open(io.BytesIO(raw))
        info["original_size"] = [image.width, image.height]
        original_mode = image.mode
        image = image.convert("RGB")
        max_side = max(image.width, image.height)
        if max_side > max_input_size:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail((max_input_size, max_input_size), resampling)
            info["resized"] = True
        should_reencode = info["resized"] or source_mime.lower() not in {"image/jpeg", "image/jpg"} or original_mode != "RGB"
        if not should_reencode:
            info["prepared_size"] = [image.width, image.height]
            return info

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90, optimize=True)
        prepared = buffer.getvalue()
        if not info["resized"] and len(prepared) >= len(raw):
            info["prepared_size"] = [image.width, image.height]
            return info

        prepared_data_url = "data:image/jpeg;base64," + base64.b64encode(prepared).decode("ascii")
        info.update(
            {
                "data_url": prepared_data_url,
                "prepared_bytes": len(prepared),
                "prepared_size": [image.width, image.height],
                "prepared_mime": "image/jpeg",
                "reencoded": True,
            }
        )
        return info
    except Exception as error:
        info["error"] = f"prepare_failed:{error.__class__.__name__}"
        return info


def public_image_prepare_info(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": info.get("role"),
        "original_bytes": info.get("original_bytes"),
        "prepared_bytes": info.get("prepared_bytes"),
        "original_size": info.get("original_size"),
        "prepared_size": info.get("prepared_size"),
        "prepared_mime": info.get("prepared_mime"),
        "resized": bool(info.get("resized")),
        "reencoded": bool(info.get("reencoded")),
        "error": info.get("error") or "",
    }


def upload_apimart_image(image: str, api_base: str, api_key: str, role: str) -> dict[str, Any]:
    if is_public_url(image):
        return {
            "role": role,
            "url": image,
            "uploaded": False,
            "mime": "",
            "bytes": 0,
        }
    raw, mime = decode_image_data_url(image)
    filename = f"nailmuse-{role}-{int(time.time() * 1000)}.{mime_to_extension(mime)}"
    upload_url = resolve_apimart_upload_url(api_base)
    response = post_multipart_file(upload_url, "file", filename, raw, mime, api_key, timeout=90)
    url = extract_upload_image_url(response)
    if not url:
        raise RuntimeError(f"upload response missing image url: {format_response_for_log(response)}")
    return {
        "role": role,
        "url": url,
        "uploaded": True,
        "mime": mime,
        "bytes": len(raw),
    }


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("image must be a public URL or data URL")
    header, payload = data_url.split(",", 1)
    mime = header.split(";", 1)[0].removeprefix("data:") or "image/png"
    return base64.b64decode(payload, validate=False), mime


def is_public_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def mime_to_extension(mime: str) -> str:
    normalized = mime.lower().split(";", 1)[0].strip()
    if normalized in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if normalized == "image/webp":
        return "webp"
    if normalized == "image/gif":
        return "gif"
    return "png"


def public_uploaded_image_info(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": info.get("role"),
        "uploaded": bool(info.get("uploaded")),
        "mime": info.get("mime") or "",
        "bytes": info.get("bytes") or 0,
        "url_host": extract_url_host(str(info.get("url") or "")),
    }


def extract_url_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""


def resolve_apimart_upload_url(api_base: str) -> str:
    root = resolve_apimart_v1_root(api_base)
    return f"{root}/uploads/images"


def resolve_apimart_v1_root(api_base: str) -> str:
    trimmed = api_base.rstrip("/")
    if trimmed.endswith("/images/generations"):
        return trimmed.removesuffix("/images/generations")
    parts = trimmed.split("/v1/", 1)
    if len(parts) == 2:
        return f"{parts[0]}/v1"
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def post_multipart_file(
    url: str,
    field_name: str,
    filename: str,
    data: bytes,
    mime: str,
    api_key: str,
    timeout: float | None = 90,
) -> dict[str, Any]:
    boundary = f"----NailMuse{hashlib.sha256(data[:1024] + str(time.time()).encode()).hexdigest()[:24]}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
            data,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{error.code} {body_text}") from error
    except urllib.error.URLError:
        return post_multipart_file_with_curl(url, field_name, filename, data, mime, api_key, timeout=timeout)


def post_multipart_file_with_curl(
    url: str,
    field_name: str,
    filename: str,
    data: bytes,
    mime: str,
    api_key: str,
    timeout: float | None = 90,
) -> dict[str, Any]:
    file_path = ""
    config_path = ""
    try:
        with __import__("tempfile").NamedTemporaryFile("wb", suffix=f".{mime_to_extension(mime)}", delete=False) as image_file:
            image_file.write(data)
            file_path = image_file.name
        curl_config = "\n".join(
            [
                'request = "POST"',
                'silent',
                'show-error',
                'fail-with-body',
                f'url = "{escape_curl_config(url)}"',
                f'header = "Authorization: Bearer {escape_curl_config(api_key)}"',
                f'form = "{field_name}=@{escape_curl_config(file_path)};filename={escape_curl_config(filename)};type={escape_curl_config(mime)}"',
            ]
        )
        with __import__("tempfile").NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
            config_file.write(curl_config)
            config_path = config_file.name
        result = __import__("subprocess").run(
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
        for path in (file_path, config_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def extract_upload_image_url(response: Any) -> str | None:
    if isinstance(response, str) and is_public_url(response):
        return response
    if isinstance(response, list):
        for item in response:
            url = extract_upload_image_url(item)
            if url:
                return url
        return None
    if not isinstance(response, dict):
        return None
    for key in ("url", "image_url", "imageUrl", "file_url", "fileUrl", "public_url", "publicUrl"):
        value = response.get(key)
        if isinstance(value, str) and is_public_url(value):
            return value
    for key in ("data", "result", "output", "response", "file", "image"):
        url = extract_upload_image_url(response.get(key))
        if url:
            return url
    return None


def format_response_for_log(response: Any) -> str:
    text = json.dumps(response, ensure_ascii=False)
    text = re.sub(r"https?://[^\"\\s]+", "https://.../[omitted]", text)
    return text[:500]


def build_style_prompt_cache_key(
    style_image_data_url: str,
    bti_result: dict[str, Any] | None,
    length_mode: str,
) -> str:
    bti_context = {}
    if isinstance(bti_result, dict):
        bti_context = {
            "code": bti_result.get("code"),
            "archetype": bti_result.get("archetype"),
            "axes": bti_result.get("axes"),
        }
    raw = json.dumps(
        {
            "style_image": style_image_data_url,
            "length_mode": length_mode,
            "bti": bti_context,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def trim_style_prompt_plan_cache(max_items: int = 32) -> None:
    while len(STYLE_PROMPT_PLAN_CACHE) > max_items:
        first_key = next(iter(STYLE_PROMPT_PLAN_CACHE))
        STYLE_PROMPT_PLAN_CACHE.pop(first_key, None)


def load_ui_style_prompt_plan(style_id: str | None, style_name: str | None) -> dict[str, Any] | None:
    bundle = load_ui_prompt_plan_bundle()
    if not bundle:
        return None
    candidate: Any = None
    normalized_id = str(style_id or "").strip()
    normalized_name = str(style_name or "").strip()
    by_id = bundle.get("byId") if isinstance(bundle.get("byId"), dict) else {}
    by_name = bundle.get("byName") if isinstance(bundle.get("byName"), dict) else {}
    if normalized_id:
        candidate = by_id.get(normalized_id)
    if not candidate and normalized_name:
        candidate = by_name.get(normalized_name)
    if not isinstance(candidate, dict):
        return None
    prompt_plan = candidate.get("promptPlan")
    if not isinstance(prompt_plan, dict):
        return None
    if str(prompt_plan.get("schema_version") or "") != "nail_tryon_prompt_plan_v1":
        return None
    return prompt_plan


def load_ui_prompt_plan_bundle() -> dict[str, Any] | None:
    global UI_PROMPT_PLAN_BUNDLE_CACHE
    if UI_PROMPT_PLAN_BUNDLE_CACHE is not None:
        return UI_PROMPT_PLAN_BUNDLE_CACHE
    if not UI_PROMPT_PLAN_BUNDLE_PATH.exists():
        UI_PROMPT_PLAN_BUNDLE_CACHE = {}
        return None
    try:
        parsed = json.loads(UI_PROMPT_PLAN_BUNDLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        UI_PROMPT_PLAN_BUNDLE_CACHE = {}
        return None
    UI_PROMPT_PLAN_BUNDLE_CACHE = parsed if isinstance(parsed, dict) else {}
    return UI_PROMPT_PLAN_BUNDLE_CACHE


def ui_prompt_plan_bundle_count() -> int:
    bundle = load_ui_prompt_plan_bundle()
    styles = bundle.get("styles") if isinstance(bundle, dict) else None
    return len(styles) if isinstance(styles, list) else 0


def public_style_prompt_plan(style_prompt_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(style_prompt_plan, dict):
        return None
    return {
        "schema_version": style_prompt_plan.get("schema_version"),
        "overall_style": style_prompt_plan.get("overall_style"),
        "confidence": style_prompt_plan.get("confidence"),
        "style_spec": style_prompt_plan.get("style_spec"),
        "finger_designs": style_prompt_plan.get("finger_designs"),
        "decoration_locks": style_prompt_plan.get("decoration_locks"),
        "transfer_priority": style_prompt_plan.get("transfer_priority"),
        "tryon_prompt": style_prompt_plan.get("tryon_prompt"),
        "negative_prompt": style_prompt_plan.get("negative_prompt"),
        "quality_checks": style_prompt_plan.get("quality_checks"),
    }


def transfer_response(final_image: str, draft_tryon_image: str | None = None) -> dict[str, str]:
    return {
        "final_tryon_image": final_image,
        "raw_tryon_image": draft_tryon_image or final_image,
        "refined_tryon_image": final_image,
    }


def extract_image(response: dict[str, Any]) -> str | None:
    if isinstance(response.get("image"), str):
        return response["image"]
    if isinstance(response.get("url"), str):
        return response["url"]
    if isinstance(response.get("image_url"), str):
        return response["image_url"]
    if isinstance(response.get("result_url"), str):
        return response["result_url"]
    for key in ("result", "output", "response"):
        nested = response.get(key)
        if isinstance(nested, dict):
            extracted = extract_image(nested)
            if extracted:
                return extracted
        elif isinstance(nested, list):
            extracted = extract_image_from_images(nested)
            if extracted:
                return extracted
    images = response.get("images") or response.get("image_urls")
    extracted = extract_image_from_images(images)
    if extracted:
        return extracted
    result = response.get("result")
    if isinstance(result, dict):
        extracted = extract_image(result)
        if extracted:
            return extracted
        images = result.get("images")
        extracted = extract_image_from_images(images)
        if extracted:
            return extracted
    data = response.get("data")
    if isinstance(data, dict):
        extracted = extract_image(data)
        if extracted:
            return extracted
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                extracted = extract_image(item)
                if extracted:
                    return extracted
    return None


def extract_image_from_images(images: Any) -> str | None:
    if not isinstance(images, list):
        return None
    for item in images:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str):
            return url
        if isinstance(url, list):
            for candidate in url:
                if isinstance(candidate, str):
                    return candidate
        if isinstance(item.get("b64_json"), str):
            return f"data:image/png;base64,{item['b64_json']}"
    return None


def extract_task_id(response: dict[str, Any]) -> str | None:
    if isinstance(response.get("task_id"), str):
        return response["task_id"]
    if isinstance(response.get("id"), str) and response["id"].startswith("task_"):
        return response["id"]
    data = response.get("data")
    if isinstance(data, dict):
        return extract_task_id(data)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                task_id = extract_task_id(item)
                if task_id:
                    return task_id
    return None


def wait_for_apimart_image(
    submit_response: dict[str, Any],
    api_base: str,
    api_key: str,
    timeout: float | None,
) -> str | None:
    image = extract_image(submit_response)
    if image:
        return image

    task_id = extract_task_id(submit_response)
    if not task_id:
        return None

    deadline = None if timeout is None or timeout <= 0 else time.monotonic() + max(30, timeout)
    task_url = resolve_apimart_task_url(api_base, task_id)
    last_status = ""
    while deadline is None or time.monotonic() < deadline:
        time.sleep(5)
        try:
            task_response = get_json(task_url, api_key, timeout=30)
        except Exception as error:
            last_status = f"poll network error: {error}"
            print(f"APIMart task poll retry: {task_id} {error}")
            continue
        image = extract_image(task_response)
        if image:
            return image

        status = extract_task_status(task_response)
        if status:
            last_status = status
        if status in APIMART_FAILED_STATUSES:
            detail = extract_task_error_detail(task_response)
            suffix = f" ({detail})" if detail else ""
            raise RuntimeError(f"APIMart task failed: {status}{suffix}")

    raise RuntimeError(f"APIMart task timed out: {task_id} {last_status}".strip())


def extract_task_status(response: dict[str, Any]) -> str:
    status = response.get("status")
    if isinstance(status, str):
        return status.lower()
    data = response.get("data")
    if isinstance(data, dict):
        return extract_task_status(data)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                nested = extract_task_status(item)
                if nested:
                    return nested
    return ""


def extract_task_error_detail(response: Any, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, list):
        for item in response:
            detail = extract_task_error_detail(item, depth + 1)
            if detail:
                return detail
        return ""
    if not isinstance(response, dict):
        return ""

    for key in (
        "error",
        "error_message",
        "error_msg",
        "message",
        "msg",
        "reason",
        "failed_reason",
        "fail_reason",
        "status_detail",
        "detail",
    ):
        value = response.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in APIMART_FAILED_STATUSES:
            return value.strip()
        if isinstance(value, dict):
            nested = extract_task_error_detail(value, depth + 1)
            if nested:
                return nested

    for key in ("data", "result", "output", "response"):
        nested = extract_task_error_detail(response.get(key), depth + 1)
        if nested:
            return nested
    return ""


def resolve_apimart_task_url(api_base: str, task_id: str) -> str:
    trimmed = api_base.rstrip("/")
    if trimmed.endswith("/images/generations"):
        root = trimmed.removesuffix("/images/generations")
    else:
        parts = trimmed.split("/v1/", 1)
        root = f"{parts[0]}/v1" if len(parts) == 2 else "https://api.aishuch.com/v1"
    return f"{root}/tasks/{task_id}"


def get_json(url: str, api_key: str, timeout: float | None = 30) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{error.code} {body}") from error
    except urllib.error.URLError:
        return get_json_with_curl(url, api_key, timeout=timeout)


def escape_curl_config(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def get_json_with_curl(url: str, api_key: str, timeout: float | None = 30) -> dict[str, Any]:
    config_path = ""
    try:
        curl_config = "\n".join(
            [
                'request = "GET"',
                'silent',
                'show-error',
                'fail-with-body',
                f'url = "{escape_curl_config(url)}"',
                f'header = "Authorization: Bearer {escape_curl_config(api_key)}"',
                'header = "Content-Type: application/json"',
            ]
        )
        with __import__("tempfile").NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
            config_file.write(curl_config)
            config_path = config_file.name

        result = __import__("subprocess").run(
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
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass


def v2_failure(
    error: str,
    started_at: float,
    timings: dict[str, int] | None = None,
    diagnostics: dict[str, Any] | None = None,
    style_prompt_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "mode": "ai_generate_v2",
        "error": error,
        "latency_ms": elapsed_ms(started_at),
    }
    if timings is not None:
        result["timings"] = timings
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    public_plan = public_style_prompt_plan(style_prompt_plan)
    if public_plan:
        result["style_prompt_plan"] = public_plan
    return result


def format_upstream_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    message = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "data:image/...;base64,[omitted]", message)
    message = re.sub(r"\s+", " ", message)
    if len(message) > 500:
        message = f"{message[:500]}..."
    return message


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def infer_style_name(prompt: str) -> str:
    text = prompt.strip()
    if "猫眼" in text and ("夏" in text or "冰" in text):
        return "夏日冰透细闪猫眼"
    if "法式" in text:
        return "清透显白轻法式"
    if "珍珠" in text:
        return "奶白珍珠通勤甲"
    if "钻" in text or "轻奢" in text:
        return "细钻轻奢显白甲"
    return "AI 定制显白美甲"


def infer_style_tags(prompt: str, bti_result: dict[str, Any] | None = None) -> list[str]:
    text = prompt.strip()
    candidates = [
        "显白",
        "短甲友好",
        "细闪",
        "夏日",
        "猫眼",
        "法式",
        "珍珠",
        "轻奢",
        "通勤",
        "冰透",
        "裸粉",
        "低饱和",
    ]
    tags = [tag for tag in candidates if tag in text]
    axes = bti_result.get("axes") if isinstance(bti_result, dict) else None
    if isinstance(axes, dict):
        if axes.get("shape_axis") == "natural_shape":
            tags.append("短甲友好")
        if axes.get("white_axis") == "contrast_white":
            tags.append("冷调提亮")
        if axes.get("white_axis") == "soft_white":
            tags.append("柔和显白")
    fallback = ["显白", "短甲友好", "细闪", "可落地"]
    deduped = []
    for tag in tags + fallback:
        if tag not in deduped:
            deduped.append(tag)
    return deduped[:5]


def infer_style_description(prompt: str, tags: list[str]) -> str:
    if "猫眼" in tags:
        return "低饱和底色搭配细闪猫眼，显白耐看，适合日常和夏日场景。"
    if "法式" in tags:
        return "清透底色配轻法式边，保留干净感，也方便门店复刻。"
    return "根据你的描述生成的可落地美甲款式，强调显白、耐看和门店可复刻。"


def infer_bti_fit_reason(bti_result: dict[str, Any] | None, tags: list[str]) -> str:
    if not isinstance(bti_result, dict):
        return "已按显白、修手和日常可落地需求生成。"
    code = bti_result.get("code") or bti_result.get("archetype") or "你的 Nail-BTI"
    return f"与你的 {code} 显白指数、修手指数和甲面承载度匹配。"


def parse_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_bool(value: str, fallback: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return fallback


def normalize_apimart_size(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "square": "1:1",
        "1024": "1:1",
        "1024x1024": "1:1",
        "1k": "1:1",
        "2k": "1:1",
        "3k": "1:1",
        "4k": "1:1",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "2:1", "1:2", "3:1", "1:3", "21:9", "9:21", "auto"}:
        return normalized
    if "x" in normalized:
        left, right = normalized.split("x", 1)
        if left.isdigit() and right.isdigit():
            width = int(left)
            height = int(right)
            if width == height:
                return "1:1"
            return "16:9" if width > height else "9:16"
    return DEFAULT_APIMART_SIZE


def normalize_apimart_resolution(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"1k", "2k", "4k"}:
        return normalized
    if normalized in {"1024", "1024x1024"}:
        return "1k"
    return DEFAULT_APIMART_RESOLUTION


def normalize_length_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"fit_user_nails", "fit-user-nails", "user"}:
        return "fit_user_nails"
    return "match_reference"
