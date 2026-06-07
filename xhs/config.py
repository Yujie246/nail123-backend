import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent


def env_value(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    for env_path in (ROOT / ".env", PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() == name:
                return raw_value.strip().strip('"').strip("'")
    return ""


KEYWORD = "美甲"
LIMIT = 20

BASE_URL = "https://www.xiaohongshu.com"
SEARCH_URL = f"{BASE_URL}/search_result?keyword={KEYWORD}"

# All outputs are written under timestamped folders:
# ./runs/YYYYMMDD_HHMMSS/
OUTPUT_ROOT = Path(os.environ.get("XHS_OUTPUT_ROOT", ROOT))
RUNS_DIR = OUTPUT_ROOT / "runs"

# Shared browser state. This is intentionally outside run folders so login can be reused.
SHARED_DATA_DIR = OUTPUT_ROOT / "data"
STORAGE_STATE = SHARED_DATA_DIR / "xhs_storage_state.json"

# Keep browser visible so you can log in manually if Xiaohongshu asks.
HEADLESS = False

# Search and filtering.
# 90 days are kept for trend analysis; only 30-day items download images for display.
ANALYSIS_DAYS = 90
RECENT_DAYS = 30
CANDIDATE_LIMIT = 80
INCLUDE_UNKNOWN_DATE = False
LOGIN_CHECK_TIMEOUT_SECONDS = 180

# Slow down page actions to avoid aggressive request patterns.
MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 5.0
SCROLL_DELAY_SECONDS = 3.0
MAX_SCROLL_ROUNDS = 35
IMAGE_DOWNLOAD_DELAY_SECONDS = 1.5
VISION_API_DELAY_SECONDS = 1.0

# Image content screening with Doubao / Ark compatible chat completions API.
FILTER_IMAGES_WITH_VISION = True
ARK_API_KEY_ENV = "ARK_API_KEY"
ARK_API_KEY = env_value(ARK_API_KEY_ENV) or env_value("DOUBAO_API_KEY")
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
ARK_MODEL = "doubao-seed-2-0-mini-260428"
ARK_TIMEOUT_SECONDS = 90
