from __future__ import annotations

import base64
import io
import os
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PIL import Image

from mobile_sam import SamPredictor, sam_model_registry


MODEL_URL = "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt"
DEFAULT_CHECKPOINT = Path(__file__).resolve().parent.parent / "models" / "mobile_sam.pt"


class Prompt(BaseModel):
    fingerTip: int
    point: list[float]
    box: list[float]
    label: Optional[str] = "fingernail"
    angle: Optional[float] = None


class SegmentRequest(BaseModel):
    image: str
    prompts: list[Prompt]


app = FastAPI(title="NailVerse MobileSAM Nail Segmenter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

predictor: SamPredictor | None = None
device = "mps" if torch.backends.mps.is_available() else "cpu"


@app.on_event("startup")
def load_model() -> None:
    global predictor
    checkpoint = Path(os.environ.get("MOBILE_SAM_CHECKPOINT", DEFAULT_CHECKPOINT))
    ensure_checkpoint(checkpoint)
    model = sam_model_registry["vit_t"](checkpoint=str(checkpoint))
    model.to(device=device)
    model.eval()
    predictor = SamPredictor(model)
    print(f"MobileSAM loaded on {device}: {checkpoint}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "device": device}


@app.post("/segment-nails")
def segment_nails(request: SegmentRequest) -> dict[str, list[dict[str, object]]]:
    if predictor is None:
        raise HTTPException(status_code=503, detail="MobileSAM is not loaded")

    image = decode_image(request.image)
    predictor.set_image(image)
    regions: list[dict[str, object]] = []

    for prompt in request.prompts:
        box = sanitize_box(prompt.box, image.shape[1], image.shape[0])
        point = np.array([prompt.point], dtype=np.float32)
        point_labels = np.array([1], dtype=np.int32)
        masks, scores, _ = predictor.predict(
            point_coords=point,
            point_labels=point_labels,
            box=np.array(box, dtype=np.float32),
            multimask_output=True,
        )
        best_index = int(np.argmax(scores))
        mask = postprocess_mask(masks[best_index], box)
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
                "confidence": float(scores[best_index]),
                "mask": encode_mask_png(mask, bbox),
            }
        )

    return {"regions": regions}


def ensure_checkpoint(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading MobileSAM checkpoint to {path}")
    urllib.request.urlretrieve(MODEL_URL, path)


def decode_image(data_url: str) -> np.ndarray:
    try:
      payload = data_url.split(",", 1)[1] if "," in data_url else data_url
      image_bytes = base64.b64decode(payload)
      image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
      return np.array(image)
    except Exception as exc:
      raise HTTPException(status_code=400, detail="Invalid image payload") from exc


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


def postprocess_mask(mask: np.ndarray, box: list[float]) -> np.ndarray:
    mask = mask.astype(np.uint8)
    limited = np.zeros_like(mask)
    x1, y1, x2, y2 = [int(round(value)) for value in box]
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


if __name__ == "__main__":
    uvicorn.run("mobile_sam_service:app", host="127.0.0.1", port=8000, reload=False)
