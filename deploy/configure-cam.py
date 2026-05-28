"""
configure-cam.py - Setze local_key fuer eine Jarnex-Cam und teste den
Light-Roundtrip gegen das Live-Modul auf VM 155.

Pre-Bedingung:
  - jarvis-module-jarnex-admin ist installed + enabled (siehe Phase 2)
  - Cam ist in der Modul-DB (z.B. id=1)
  - local_key wurde aus iot.tuya.com extrahiert (32 Hex-Char)

Sicherheit:
  - local_key wird NICHT geprintet (nur first4+last4 als visual confirm)
  - Skript fragt interaktiv via getpass falls JARNEX_LOCAL_KEY nicht gesetzt

Aufruf (3 Wege):
  1. Doppelklick auf configure-cam.bat im Explorer (Win)
  2. ENV-basiert:
       JARNEX_LOCAL_KEY=<32hex> python configure-cam.py
  3. Interaktiv:
       python configure-cam.py
       (fragt dann nach Key via getpass)

ENV-Variablen (alle optional ausser JARNEX_LOCAL_KEY):
  JARNEX_LOCAL_KEY      32-Hex-Char-Key aus iot.tuya.com (Pflicht)
  JARNEX_DEVICE_ID      Tuya-Device-UUID (optional - manche Cams brauchen das)
  JARNEX_TUYA_VERSION   3.3 (default), 3.4 oder 3.5 je nach Firmware
  JARNEX_CAM_ID         DB-Cam-ID auf VM 155 (default 1)
  JARNEX_API_BASE       default http://192.168.10.11:8300/modules/jarnex-admin/api
  JARNEX_BRIGHTNESS     default 80
  JARNEX_LIGHT_HOLD_S   wie lange Licht an bleiben (default 4)
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


API_BASE = os.environ.get(
    "JARNEX_API_BASE",
    "http://192.168.10.11:8300/modules/jarnex-admin/api",
).rstrip("/")
CAM_ID = int(os.environ.get("JARNEX_CAM_ID", "1"))
TUYA_VERSION = os.environ.get("JARNEX_TUYA_VERSION", "3.3")
BRIGHTNESS = int(os.environ.get("JARNEX_BRIGHTNESS", "80"))
HOLD_S = float(os.environ.get("JARNEX_LIGHT_HOLD_S", "4"))


def fail(msg: str, exit_code: int = 1) -> "NoReturn":
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def info(msg: str) -> None:
    print(f"  {msg}")


def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}")


def redact_key(k: str) -> str:
    if len(k) < 12:
        return "***"
    return f"{k[:4]}...{k[-4:]} (len={len(k)})"


def http_call(method: str, path: str, body: dict | None = None, timeout: float = 8.0) -> dict:
    """Synchroner HTTP-Call gegen das Modul. Returns parsed JSON dict."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300]
        fail(f"HTTP {e.code} bei {method} {path}: {body_text}")
    except urllib.error.URLError as e:
        fail(f"Verbindung zu {API_BASE} fehlgeschlagen: {e.reason}")
    return {}


def prompt_local_key() -> str:
    """Hole local_key entweder aus ENV oder interaktiv via getpass.

    getpass.getpass blendet die Eingabe aus, damit Key nicht in der
    Konsolen-History landet.
    """
    env_key = os.environ.get("JARNEX_LOCAL_KEY", "").strip()
    if env_key:
        return env_key
    print("\nlocal_key aus iot.tuya.com kopieren und einfuegen.")
    print("(Eingabe wird ausgeblendet, kein History-Risiko)")
    key = getpass.getpass("local_key: ").strip()
    if not key:
        fail("kein local_key eingegeben")
    return key


def validate_local_key(k: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{16,32}", k):
        fail(
            f"local_key hat unerwartetes Format ({len(k)} Zeichen). "
            "Erwartet werden 16-32 Hex-Zeichen aus iot.tuya.com."
        )


def main() -> int:
    print(f"API-Base : {API_BASE}")
    print(f"Cam-ID   : {CAM_ID}")
    print(f"Tuya-Ver : {TUYA_VERSION}")

    step(1, "Health-Probe gegen Live-Modul")
    health = http_call("GET", "/health")
    if health.get("status") != "ok":
        fail(f"Modul nicht gesund: {health}")
    info(f"db_ok={health['db_ok']}  camera_count={health['camera_count']}")

    step(2, "Cam-Existenz pruefen")
    cam = http_call("GET", f"/cameras/{CAM_ID}")
    info(f"name={cam['name']}  host={cam['host']}  backend={cam['backend']}")
    if cam["backend"] != "tuya_lan":
        fail(f"Cam {CAM_ID} ist backend={cam['backend']}, nicht tuya_lan")

    step(3, "local_key holen + validieren")
    key = prompt_local_key()
    validate_local_key(key)
    device_id = os.environ.get("JARNEX_DEVICE_ID", "").strip()
    info(f"key: {redact_key(key)}")
    if device_id:
        info(f"device_id: {device_id}")

    step(4, "PATCH credentials")
    creds_body: dict = {"local_key": key, "tuya_version": TUYA_VERSION}
    r = http_call("PATCH", f"/cameras/{CAM_ID}/credentials", body=creds_body)
    if not r.get("updated"):
        fail(f"PATCH credentials fehlgeschlagen: {r}")
    info("credentials aktualisiert")

    if device_id and not cam.get("device_id"):
        step(4.5, "device_id setzen")
        http_call("PATCH", f"/cameras/{CAM_ID}", body={"device_id": device_id})
        info(f"device_id={device_id} hinterlegt")

    step(5, "Capabilities-Refresh (Live-Probe gegen Cam)")
    caps = http_call("GET", f"/cameras/{CAM_ID}/capabilities?refresh=true", timeout=15.0)
    summary = caps.get("summary") or {}
    if not summary:
        fail(f"Capabilities-Probe lieferte leeres Summary: {caps}")
    info(f"backend_id    : {summary.get('backend_id')}")
    info(f"live_probe_ok : {summary.get('live_probe_ok')}")
    info(f"has_light     : {summary.get('has_light')}")
    info(f"has_ptz       : {summary.get('has_ptz')}")
    if not summary.get("live_probe_ok"):
        fail(
            "Live-Probe gegen Cam fehlgeschlagen. "
            "local_key falsch? tuya_version (3.3 vs 3.4) anders? "
            "Cam erreichbar? jarvis-admin venv hat tinytuya installiert?"
        )

    step(6, f"Light AN (brightness={BRIGHTNESS})")
    r = http_call("POST", f"/cameras/{CAM_ID}/light",
                  body={"on": True, "brightness": BRIGHTNESS}, timeout=12.0)
    info("Licht-an angefordert. Schaue zur Cam!")

    step(7, f"Halte {HOLD_S}s")
    time.sleep(HOLD_S)

    step(8, "State-Read")
    state = http_call("GET", f"/cameras/{CAM_ID}/state", timeout=12.0)
    info(f"state: {json.dumps(state.get('state', {}), ensure_ascii=False)[:200]}")

    step(9, "Light AUS")
    http_call("POST", f"/cameras/{CAM_ID}/light",
              body={"on": False}, timeout=12.0)
    info("Licht-aus angefordert")

    step(10, "Final-State")
    state_off = http_call("GET", f"/cameras/{CAM_ID}/state", timeout=12.0)
    info(f"state: {json.dumps(state_off.get('state', {}), ensure_ascii=False)[:200]}")

    print("\nRoundtrip erfolgreich. Cam ist betriebsbereit.")
    print(f"UI: http://192.168.10.11:8300/modules/jarnex-admin/ui/")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        sys.exit(130)
