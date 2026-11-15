"""Server configuration. Users never log in; the server may hold one Earthdata token."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EARTHDATA_TOKEN = os.getenv("EARTHDATA_TOKEN", "").strip()
EARTHDATA_USERNAME = os.getenv("EARTHDATA_USERNAME", "").strip()
EARTHDATA_PASSWORD = os.getenv("EARTHDATA_PASSWORD", "").strip()
CACHE_DIR = Path(os.getenv("CACHE_DIR", ROOT / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", ROOT.parent / "frontend" / "dist"))
USER_AGENT = "EarthEcho/0.1 (NASA Space Apps 2026 open-source project)"
MAX_DATES = int(os.getenv("MAX_DATES", "12"))


def has_earthdata() -> bool:
    return bool(EARTHDATA_TOKEN or (EARTHDATA_USERNAME and EARTHDATA_PASSWORD))
