"""crashy: crashes natively (so Windows logs Application Error 1000) when its cache file is
corrupt, and runs normally when the cache is healthy or missing. Lab scenario S3.

    python crashy.py --corrupt   write a corrupt cache file and exit
    python crashy.py             start; crash if the cache is corrupt
"""
import ctypes
import json
import os
import sys
import time
from pathlib import Path

CACHE = Path(os.environ["LOCALAPPDATA"]) / "Crashy" / "cache" / "state.json"

if "--corrupt" in sys.argv:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(b"\x00\xffnot json")
    sys.exit(0)

if CACHE.exists():
    try:
        json.loads(CACHE.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        ctypes.string_at(0)  # access violation: a real native crash, logged by Windows

print("crashy is running fine")
time.sleep(int(os.environ.get("CRASHY_SECONDS", "120")))
