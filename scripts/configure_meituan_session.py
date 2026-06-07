from __future__ import annotations

import getpass
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = PROJECT_ROOT / "backend" / ".meituan_session.json"


def main() -> None:
    print("Paste your Meituan Cookie header. Input is hidden and will be saved locally only.")
    cookie = getpass.getpass("MEITUAN_COOKIE: ").strip()
    if not cookie:
        raise SystemExit("No cookie provided; nothing was written.")
    SESSION_PATH.write_text(json.dumps({"cookie": cookie}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved local Meituan session to {SESSION_PATH}")


if __name__ == "__main__":
    main()
