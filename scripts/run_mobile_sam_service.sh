#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv-mobile-sam" ]; then
  python3 -m venv .venv-mobile-sam
fi

source .venv-mobile-sam/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-mobile-sam.txt
python services/mobile_sam_service.py
