#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv-yolo" ]; then
  python3 -m venv .venv-yolo
fi

source .venv-yolo/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-yolo.txt

DATASET_YAML="${1:-datasets/nail-bed-yolo.yaml}"
EPOCHS="${2:-20}"
IMGSZ="${3:-640}"
PATIENCE="${4:-50}"
MODEL="${5:-yolov8n-seg.pt}"

yolo segment train \
  model="$MODEL" \
  data="$DATASET_YAML" \
  epochs="$EPOCHS" \
  imgsz="$IMGSZ" \
  batch=4 \
  patience="$PATIENCE" \
  workers=2 \
  project=runs/nail-bed-seg \
  name=yolov8n-overfit \
  exist_ok=true
