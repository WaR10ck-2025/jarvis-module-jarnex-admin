"""
_test_server.py - jarvis-admin-Wrapper fuer Lokal-Dev.

Spawnt einen lokalen uvicorn auf 127.0.0.1:8403 mit demselben Mount-Pfad-Schema
wie auf VM 155 (/modules/jarnex-admin/api + /ui). UI + Tests koennen dieselbe
API_BASE-Replace-Logik nutzen wie in Production.

Aufruf:
  cd C:\\Daten\\Projekte\\jarvis-module-jarnex-admin
  python _test_server.py
  # oder: TEST_SERVER_PORT=8503 python _test_server.py

Smoke:
  curl http://127.0.0.1:8403/modules/jarnex-admin/api/health
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS))

# Test-Mode-ENV: data.db neben router.py
os.environ.setdefault("JARNEX_DB_PATH", str(THIS / "data.db"))
os.environ.setdefault("JARNEX_FRIGATE_MODULE_URL", "http://192.168.10.11:8300/modules/frigate")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from router import router

MODULE_ID = "jarnex-admin"
PREFIX = f"/modules/{MODULE_ID}"

app = FastAPI(title="jarvis-module-jarnex-admin (Lokal-Dev)")
app.include_router(router, prefix=f"{PREFIX}/api")

_static_dir = THIS / "static"
if _static_dir.exists():
    app.mount(f"{PREFIX}/ui", StaticFiles(directory=str(_static_dir), html=True), name="jarnex-ui")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "module": MODULE_ID,
        "ui": f"{PREFIX}/ui/",
        "api": f"{PREFIX}/api/health",
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("TEST_SERVER_PORT", "8403"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
