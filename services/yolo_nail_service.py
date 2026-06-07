from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel
from ultralytics import YOLO


DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "yolo_nail_best.pt"


class Prompt(BaseModel):
    fingerTip: int
    point: list[float]
    box: list[float]
    label: Optional[str] = "fingernail"
    angle: Optional[float] = None


class SegmentRequest(BaseModel):
    image: str
    prompts: list[Prompt]


class DetectRequest(BaseModel):
    image: str


app = FastAPI(title="NailVerse YOLO Nail Segmenter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

model: YOLO | None = None
model_path: Path | None = None


@app.on_event("startup")
def load_model() -> None:
    global model, model_path
    model_path = Path(os.environ.get("YOLO_NAIL_MODEL", DEFAULT_MODEL))
    if not model_path.exists():
        print(f"YOLO nail model not found: {model_path}")
        model = None
        return
    model = YOLO(str(model_path))
    print(f"YOLO nail segmenter loaded: {model_path}")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if model is not None else "missing_model",
        "model": str(model_path or DEFAULT_MODEL),
        "loaded": model is not None,
    }


@app.post("/segment-nails")
def segment_nails(request: SegmentRequest) -> dict[str, list[dict[str, object]]]:
    if model is None:
        raise HTTPException(status_code=503, detail=f"YOLO model is not loaded. Put best.pt at {DEFAULT_MODEL}")

    image = decode_image(request.image)
    candidates = predict_candidates(image)
    regions: list[dict[str, object]] = []
    used: set[int] = set()

    for prompt in request.prompts:
        box = sanitize_box(prompt.box, image.shape[1], image.shape[0])
        match = pick_best_candidate(candidates, box, prompt.point, used)
        if match is None:
            continue
        used.add(match["index"])
        mask = postprocess_mask(match["mask"], box)
        bbox = mask_bbox(mask)
        if bbox is None:
            continue
        x, y, width, height = bbox
        regions.append(
            {
                "fingerTip": prompt.fingerTip,
                "bbox": [x, y, width, height],
                "centerX": x + width / 2,
                "centerY": y + height / 2,
                "width": width,
                "height": height,
                "angle": prompt.angle,
                "confidence": match["confidence"],
                "mask": encode_mask_png(mask, bbox),
            }
        )

    return {"regions": regions}


@app.post("/detect-nails")
def detect_nails(request: DetectRequest) -> dict[str, list[dict[str, object]]]:
    if model is None:
        raise HTTPException(status_code=503, detail=f"YOLO model is not loaded. Put best.pt at {DEFAULT_MODEL}")

    image = decode_image(request.image)
    candidates = predict_candidates(image)
    regions: list[dict[str, object]] = []

    for candidate in sorted(candidates, key=lambda item: (item["box"][0] + item["box"][2]) / 2):
        mask = largest_component(candidate["mask"])
        bbox = mask_bbox(mask)
        if bbox is None:
            continue
        x, y, width, height = bbox
        regions.append(
            {
                "bbox": [x, y, width, height],
                "centerX": x + width / 2,
                "centerY": y + height / 2,
                "width": width,
                "height": height,
                "angle": 0,
                "confidence": candidate["confidence"],
                "mask": encode_mask_png(mask, bbox),
            }
        )

    return {"regions": regions}


def decode_image(data_url: str) -> np.ndarray:
    try:
        payload = data_url.split(",", 1)[1] if "," in data_url else data_url
        image_bytes = base64.b64decode(payload)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return np.array(image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image payload") from exc


def predict_candidates(image: np.ndarray) -> list[dict[str, object]]:
    assert model is not None
    confidence = float(os.environ.get("YOLO_NAIL_CONF", "0.18"))
    result = model.predict(image, conf=confidence, retina_masks=True, verbose=False)[0]
    if result.masks is None or result.boxes is None:
        return []

    masks = result.masks.data.detach().cpu().numpy()
    boxes = result.boxes.xyxy.detach().cpu().numpy()
    scores = result.boxes.conf.detach().cpu().numpy()
    classes = result.boxes.cls.detach().cpu().numpy() if result.boxes.cls is not None else np.zeros(len(boxes))
    image_height, image_width = image.shape[:2]
    candidates: list[dict[str, object]] = []

    for index, mask in enumerate(masks):
        resized = resize_mask(mask, image_width, image_height)
        if resized.sum() < 12:
            continue
        candidates.append(
            {
                "index": index,
                "mask": resized,
                "box": boxes[index],
                "confidence": float(scores[index]),
                "classId": int(classes[index]),
            }
        )
    return candidates


def pick_best_candidate(
    candidates: list[dict[str, object]],
    prompt_box: list[float],
    prompt_point: list[float],
    used: set[int],
) -> dict[str, object] | None:
    scored = []
    px, py = prompt_point
    prompt_area = max(1.0, (prompt_box[2] - prompt_box[0]) * (prompt_box[3] - prompt_box[1]))

    for candidate in candidates:
        if candidate["index"] in used:
            continue
        box = candidate["box"]
        iou = box_iou(prompt_box, box)
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        center_distance = np.hypot(cx - px, cy - py) / np.sqrt(prompt_area)
        point_inside = 1.0 if box[0] <= px <= box[2] and box[1] <= py <= box[3] else 0.0
        score = iou * 2.6 + point_inside * 0.8 + float(candidate["confidence"]) - center_distance * 0.45
        scored.append((score, candidate))

    if not scored:
        return None
    best_score, best = max(scored, key=lambda item: item[0])
    return best if best_score > -0.18 else None


def sanitize_box(box: list[float], width: int, height: int) -> list[float]:
    if len(box) != 4:
        raise HTTPException(status_code=400, detail="Prompt box must be [x1,y1,x2,y2]")
    x1, y1, x2, y2 = box
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(0, min(width - 1, x2))
    y2 = max(0, min(height - 1, y2))
    if x2 <= x1 or y2 <= y1:
        raise HTTPException(status_code=400, detail="Prompt box has invalid dimensions")
    return [x1, y1, x2, y2]


def resize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    if mask.shape[:2] != (height, width):
        mask = cv2.resize(mask.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)
    return (mask > 0.5).astype(np.uint8)


def postprocess_mask(mask: np.ndarray, box: list[float]) -> np.ndarray:
    limited = np.zeros_like(mask, dtype=np.uint8)
    x1, y1, x2, y2 = [int(round(value)) for value in box]
    pad_x = max(4, int((x2 - x1) * 0.18))
    pad_y = max(4, int((y2 - y1) * 0.18))
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(mask.shape[1] - 1, x2 + pad_x)
    y2 = min(mask.shape[0] - 1, y2 + pad_y)
    limited[y1:y2, x1:x2] = mask[y1:y2, x1:x2]

    kernel = np.ones((3, 3), np.uint8)
    limited = cv2.morphologyEx(limited, cv2.MORPH_OPEN, kernel)
    limited = cv2.morphologyEx(limited, cv2.MORPH_CLOSE, kernel, iterations=2)
    return largest_component(limited)


def largest_component(mask: np.ndarray) -> np.ndarray:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return mask
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == largest).astype(np.uint8)


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return None
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    return x1, y1, x2 - x1 + 1, y2 - y1 + 1


def encode_mask_png(mask: np.ndarray, bbox: tuple[int, int, int, int]) -> str:
    x, y, width, height = bbox
    crop = mask[y : y + height, x : x + width]
    alpha = (crop * 255).astype(np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def box_iou(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = [float(value) for value in a]
    bx1, by1, bx2, by2 = [float(value) for value in b]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


if __name__ == "__main__":
    uvicorn.run("yolo_nail_service:app", host="127.0.0.1", port=8001, reload=False)
