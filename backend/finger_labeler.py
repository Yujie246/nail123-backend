from __future__ import annotations

import base64
import io
import os
from typing import Any


FINGER_SPECS = {
    "thumb": {"label": "T", "tip": 4, "points": (1, 2, 3, 4)},
    "index": {"label": "I", "tip": 8, "points": (5, 6, 7, 8)},
    "middle": {"label": "M", "tip": 12, "points": (9, 10, 11, 12)},
    "ring": {"label": "R", "tip": 16, "points": (13, 14, 15, 16)},
    "pinky": {"label": "P", "tip": 20, "points": (17, 18, 19, 20)},
}


def build_finger_label_bundle(hand_image: str, style_image: str) -> dict[str, Any]:
    hand = label_fingers(hand_image, "用户手图")
    style = label_fingers(style_image, "目标款式图")
    if not hand["success"] or not style["success"]:
        return {
            "success": False,
            "error": hand.get("error") or style.get("error") or "手指识别不完整",
            "hand": hand,
            "style": style,
            "mapping": build_mapping_text(),
        }

    return {
        "success": True,
        "hand": hand,
        "style": style,
        "mapping": build_mapping_text(),
        "combined_labeled_image": combine_labeled_images(hand["labeled_image"], style["labeled_image"]),
    }


def label_fingers(image_data_url: str, title: str = "") -> dict[str, Any]:
    if os.getenv("NAIL_ENABLE_BACKEND_MEDIAPIPE", "").strip() != "1":
        return {
            "success": False,
            "error": "后端 MediaPipe 标签器默认关闭；当前使用前端 MediaPipe WASM 生成标签图。",
        }
    try:
        os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib")
        from PIL import Image, ImageDraw, ImageFont
        import mediapipe as mp
    except Exception as error:
        return {
            "success": False,
            "error": f"finger labeler dependencies missing: {error}",
        }

    try:
        image_bytes, _mime = decode_data_url(image_data_url)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_to_numpy(image))
        detector_options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path="public/mediapipe/hand_landmarker.task",
                delegate=mp.tasks.BaseOptions.Delegate.CPU,
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=0.45,
        )
        with mp.tasks.vision.HandLandmarker.create_from_options(detector_options) as detector:
            result = detector.detect(mp_image)

        if not result.hand_landmarks:
            return {"success": False, "error": "未识别到完整手部关键点"}

        landmarks = result.hand_landmarks[0]
        if len(landmarks) < 21:
            return {"success": False, "error": "手部关键点不足 21 个"}

        handedness = "unknown"
        if result.handedness and result.handedness[0]:
            handedness = str(result.handedness[0][0].category_name).lower()

        draw = ImageDraw.Draw(image)
        font = load_label_font(max(28, min(width, height) // 18))
        title_font = load_label_font(max(22, min(width, height) // 28))
        fingers: dict[str, Any] = {}

        if title:
            draw.rounded_rectangle((18, 18, min(width - 18, 18 + len(title) * 28), 64), radius=18, fill=(31, 20, 28))
            draw.text((34, 25), title, font=title_font, fill=(255, 255, 255))

        for name, spec in FINGER_SPECS.items():
            tip = normalized_to_xy(landmarks[spec["tip"]], width, height)
            center = average_points([landmarks[index] for index in spec["points"]], width, height)
            label = str(spec["label"])
            label_pos = label_position(tip, center, width, height)
            draw_label(draw, label_pos, label, font)
            fingers[name] = {
                "label": label,
                "tip": [round(tip[0]), round(tip[1])],
                "center": [round(center[0]), round(center[1])],
            }

        return {
            "success": True,
            "handedness": handedness,
            "view": "unknown",
            "fingers": fingers,
            "labeled_image": image_to_data_url(image),
        }
    except Exception as error:
        return {"success": False, "error": f"手指标签生成失败: {error}"}


def build_mapping_text() -> dict[str, str]:
    return {
        "thumb": "目标图 T 手指的款式迁移到用户图 T 手指",
        "index": "目标图 I 手指的款式迁移到用户图 I 手指",
        "middle": "目标图 M 手指的款式迁移到用户图 M 手指",
        "ring": "目标图 R 手指的款式迁移到用户图 R 手指",
        "pinky": "目标图 P 手指的款式迁移到用户图 P 手指",
    }


def decode_data_url(data_url: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("image must be a data URL")
    header, payload = data_url.split(",", 1)
    mime = header.split(";", 1)[0].removeprefix("data:")
    return base64.b64decode(payload), mime


def image_to_numpy(image: Any) -> Any:
    import numpy as np

    return np.asarray(image)


def normalized_to_xy(point: Any, width: int, height: int) -> tuple[float, float]:
    return (float(point.x) * width, float(point.y) * height)


def average_points(points: list[Any], width: int, height: int) -> tuple[float, float]:
    xs = [float(point.x) * width for point in points]
    ys = [float(point.y) * height for point in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def label_position(tip: tuple[float, float], center: tuple[float, float], width: int, height: int) -> tuple[int, int]:
    vx = tip[0] - center[0]
    vy = tip[1] - center[1]
    length = max((vx * vx + vy * vy) ** 0.5, 1)
    radius = max(34, min(width, height) // 16)
    x = tip[0] + vx / length * radius
    y = tip[1] + vy / length * radius
    margin = radius + 8
    return (round(max(margin, min(width - margin, x))), round(max(margin, min(height - margin, y))))


def draw_label(draw: Any, center: tuple[int, int], label: str, font: Any) -> None:
    radius = max(28, font.size)
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(216, 58, 100), outline=(255, 255, 255), width=6)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text((x - text_width / 2, y - text_height / 2 - 2), label, font=font, fill=(255, 255, 255))


def load_label_font(size: int) -> Any:
    from PIL import ImageFont

    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def image_to_data_url(image: Any) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def combine_labeled_images(hand_labeled_image: str, style_labeled_image: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    hand_bytes, _ = decode_data_url(hand_labeled_image)
    style_bytes, _ = decode_data_url(style_labeled_image)
    hand = Image.open(io.BytesIO(hand_bytes)).convert("RGB")
    style = Image.open(io.BytesIO(style_bytes)).convert("RGB")
    tile_width = 768
    title_height = 70
    hand = resize_contain(hand, tile_width, tile_width)
    style = resize_contain(style, tile_width, tile_width)
    canvas = Image.new("RGB", (tile_width * 2, tile_width + title_height), (255, 248, 243))
    canvas.paste(hand, (0, title_height))
    canvas.paste(style, (tile_width, title_height))
    draw = ImageDraw.Draw(canvas)
    font = load_label_font(32)
    draw.text((32, 20), "用户手图标签图", font=font, fill=(31, 20, 28))
    draw.text((tile_width + 32, 20), "目标款式图标签图", font=font, fill=(31, 20, 28))
    return image_to_data_url(canvas)


def resize_contain(image: Any, width: int, height: int) -> Any:
    from PIL import Image

    image = image.copy()
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    x = (width - image.width) // 2
    y = (height - image.height) // 2
    canvas.paste(image, (x, y))
    return canvas
