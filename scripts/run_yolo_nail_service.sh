#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv-yolo" ]; then
  python3 -m venv .venv-yolo
fi

source .venv-yolo/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-yolo.txt
python services/yolo_nail_service.py
