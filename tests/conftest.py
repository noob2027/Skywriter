"""Shared test configuration."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-gpu-compositing",
)
os.environ.setdefault(
    "SKYWRITER_MAP_CACHE_ROOT",
    str(Path(__file__).resolve().parents[1] / ".pytest_cache" / "webengine"),
)
