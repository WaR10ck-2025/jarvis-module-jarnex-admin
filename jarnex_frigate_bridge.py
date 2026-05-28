"""
jarnex_frigate_bridge.py - Conditional Frigate-Provision.

Macht nur Sinn wenn die Cam einen RTSP-Stream-URL hat (entweder Stock-ONVIF
oder Post-OpenIPC-Replace). Bei Tuya-only-Cams wird 412 Precondition Failed
returnt mit klarer Diagnose.

Snapshot-Polling-Alternative: Frigate kann statt RTSP einen `still_image_url`
auf den Modul-eigenen Snapshot-Endpoint zeigen lassen. Das funktioniert mit
Cloud-Backend (Snapshot via /picture-API), aber nicht mit LAN-only.

URL-Encoding-Pflicht: Reolink-Memory `feedback_frigate_go2rtc_url_encoded_rtsp_password.md`
- embedded ffmpeg/go2rtc in Frigate ist strict, alle Userinfo-Special-Chars
  muessen via urllib.parse.quote(safe="") encoded werden.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger("jarvis.module.jarnex_admin.frigate_bridge")


class FrigateProvisionError(Exception):
    """Wird vom router.py in 412 Precondition Failed konvertiert."""


def build_provision_payload(
    *,
    name: str,
    host: str,
    stream_url: Optional[str],
    rtsp_user: Optional[str],
    rtsp_pass: Optional[str],
    snapshot_url: Optional[str] = None,
    onvif_port: int = 8000,
) -> dict[str, Any]:
    """Baut das Frigate-CameraIn-Payload.

    Drei Modi:
      1. stream_url gesetzt -> RTSP-Stream provision (rtsp_main_path)
      2. snapshot_url gesetzt (kein stream) -> still_image_url provision (motion-only)
      3. weder noch -> FrigateProvisionError
    """
    if not stream_url and not snapshot_url:
        raise FrigateProvisionError(
            "Cam hat weder stream_url noch snapshot_url. "
            "Tuya-LAN-only-Cams koennen nicht direkt nach Frigate provisioniert werden. "
            "Pruefe Phase 0 Substrate-Verify oder OpenIPC-Replace."
        )

    payload: dict[str, Any] = {
        "name": name,
        "display_name": name,
        "ip": host,
        "onvif_port": onvif_port,
        "ptz_enabled": False,
        "detect_objects": "person",
        "record_enabled": True,
        "record_retain_d": 7,
        "zones_json": "{}",
        "enabled": True,
        "_meta": {
            "vendor": "jarnex",
            "model": "JNOL4",
        },
    }

    if stream_url:
        # Encoded Credentials einbetten wenn separat
        if rtsp_user and "@" not in stream_url:
            user = quote(rtsp_user or "", safe="")
            pwd = quote(rtsp_pass or "", safe="")
            if stream_url.startswith("rtsp://"):
                rest = stream_url[len("rtsp://"):]
                stream_url = f"rtsp://{user}:{pwd}@{rest}"
        payload["rtsp_main"] = stream_url
        payload["rtsp_user"] = rtsp_user or ""
        payload["rtsp_password"] = rtsp_pass or ""
        payload["_meta"]["mode"] = "rtsp"
    else:
        # Snapshot-Polling-Mode (Frigate macht 5s-Polling auf still_image_url)
        payload["still_image_url"] = snapshot_url
        payload["_meta"]["mode"] = "snapshot_polling"

    return payload


async def post_to_frigate(
    *,
    frigate_module_url: str,
    admin_api_key: Optional[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST das Payload an das Frigate-Modul. Fail-Soft mit klarer Fehlermeldung."""
    try:
        import httpx  # type: ignore
    except ImportError as e:
        raise RuntimeError("httpx nicht installiert") from e

    url = f"{frigate_module_url.rstrip('/')}/api/cameras"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if admin_api_key:
        headers["X-API-Key"] = admin_api_key

    # _meta entfernen, ist nicht Teil des Frigate-Schemas
    safe_payload = {k: v for k, v in payload.items() if k != "_meta"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=safe_payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Frigate-Bridge unreachable ({url}): {e}") from e
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Frigate POST {url} -> HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"text": resp.text}


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """RTSP-PW und ggf andere Secrets in Payload maskieren."""
    out = dict(payload)
    if "rtsp_password" in out:
        out["rtsp_password"] = "***"
    if "rtsp_main" in out and isinstance(out["rtsp_main"], str) and "@" in out["rtsp_main"]:
        url = out["rtsp_main"]
        scheme_creds, rest = url.split("@", 1)
        scheme, creds = scheme_creds.split("://", 1)
        user = creds.split(":", 1)[0] if ":" in creds else creds
        out["rtsp_main"] = f"{scheme}://{user}:***@{rest}"
    return out
