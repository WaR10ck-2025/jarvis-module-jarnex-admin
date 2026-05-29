"""
router.py - jarvis-module-jarnex-admin.

JARVIS-Admin-Modul fuer Jarnex Outdoor Porch-Light-Cameras (JNOL4 / Tuya-OEM).

Mount-Pfad: /modules/jarnex-admin/api + /modules/jarnex-admin/ui

Phase-1 (Hardware-unabhaengig): Skelett + Mocks. Alle Backend-Calls werden
gegen die im Constructor injizierten Transport-Layer geleitet, sodass
pytest ohne echte Hardware komplett gruen wird.

Konventionen-Pflicht:
  - Datei-Prefix `jarnex_*.py` gegen sys.modules-Kollision
  - data.db neben router.py (ENV JARNEX_DB_PATH override)
  - Logger jarvis.module.jarnex_admin.<file>
  - Routing-Order: spezifische Pfade VOR /{id}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

try:
    from .jarnex_database import (
        connect,
        delete_camera,
        ensure_schema,
        get_camera,
        get_capability,
        get_credentials,
        get_setting,
        insert_camera,
        list_cameras,
        list_capabilities,
        list_events,
        set_capability,
        set_setting,
        update_camera,
        update_credentials,
        VALID_BACKENDS,
    )
    from . import jarnex_discovery
    from . import jarnex_capabilities as caps_mod
    from . import jarnex_alarm_listener as alarm_mod
    from . import jarnex_frigate_bridge as frigate_bridge
    from .jarnex_backend import (
        JarnexError,
        NoBackendAvailable,
        select_backend,
    )
except ImportError:  # Lokal-Dev via _test_server.py
    from jarnex_database import (  # type: ignore[no-redef]
        connect,
        delete_camera,
        ensure_schema,
        get_camera,
        get_capability,
        get_credentials,
        get_setting,
        insert_camera,
        list_cameras,
        list_capabilities,
        list_events,
        set_capability,
        set_setting,
        update_camera,
        update_credentials,
        VALID_BACKENDS,
    )
    import jarnex_discovery  # type: ignore[no-redef]
    import jarnex_capabilities as caps_mod  # type: ignore[no-redef]
    import jarnex_alarm_listener as alarm_mod  # type: ignore[no-redef]
    import jarnex_frigate_bridge as frigate_bridge  # type: ignore[no-redef]
    from jarnex_backend import (  # type: ignore[no-redef]
        JarnexError,
        NoBackendAvailable,
        select_backend,
    )


router = APIRouter()
logger = logging.getLogger("jarvis.module.jarnex_admin.router")

MODULE_VERSION = "0.1.0"
FRIGATE_MODULE_URL = os.getenv(
    "JARNEX_FRIGATE_MODULE_URL",
    "http://localhost:8300/modules/frigate",
).rstrip("/")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
TUYA_REGION = os.getenv("JARNEX_TUYA_REGION", "eu")

# Schema beim Modul-Import sicherstellen
try:
    ensure_schema()
    logger.info("jarnex-admin DB-Schema initialisiert")
except Exception as e:  # noqa: BLE001
    logger.error("DB-Schema konnte nicht initialisiert werden: %s", e)


# ============================================================================
# Pydantic-Modelle
# ============================================================================


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1)
    device_id: Optional[str] = None
    port: int = 6668
    backend: str = "tuya_lan"
    stream_url: Optional[str] = None
    model: Optional[str] = None
    firmware: Optional[str] = None
    notes: Optional[str] = None
    local_key: Optional[str] = None
    tuya_version: str = "3.3"
    rtsp_user: Optional[str] = None
    rtsp_pass: Optional[str] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    device_id: Optional[str] = None
    port: Optional[int] = None
    backend: Optional[str] = None
    stream_url: Optional[str] = None
    model: Optional[str] = None
    firmware: Optional[str] = None
    notes: Optional[str] = None


class CredentialsUpdate(BaseModel):
    local_key: Optional[str] = None
    tuya_version: Optional[str] = None
    rtsp_user: Optional[str] = None
    rtsp_pass: Optional[str] = None


class DiscoverRequest(BaseModel):
    cidr: str = "192.168.10.0/24"
    timeout_s: float = 1.5


class PtzCommand(BaseModel):
    op: str = Field(description="One of: up, down, left, right, stop")
    duration_s: float = Field(default=1.0, ge=0.0, le=10.0)


class LightCommand(BaseModel):
    on: bool
    brightness: Optional[int] = Field(default=None, ge=0, le=100)


class SirenCommand(BaseModel):
    action: str = Field(default="play", description="play (only supported action)")


# ============================================================================
# Backend-Helper - Singleton-Cache pro Cam-ID
# ============================================================================

_backend_cache: dict[int, Any] = {}


def _resolve_cloud_settings() -> dict[str, str] | None:
    keys = (
        "tuya_cloud_project_id",
        "tuya_cloud_region",
        "tuya_cloud_access_id",
        "tuya_cloud_access_key",
    )
    out: dict[str, str] = {}
    for k in keys:
        v = get_setting(k)
        if v:
            out[k] = v
    if "tuya_cloud_access_id" in out and "tuya_cloud_access_key" in out:
        out.setdefault("tuya_cloud_region", TUYA_REGION)
        return out
    return None


def _cloud_fallback_enabled() -> bool:
    return (get_setting("cloud_fallback_enabled") or "").lower() in ("1", "true", "yes")


async def _get_backend(cam_id: int):
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    cached = _backend_cache.get(cam_id)
    if cached is not None:
        return cached
    creds = get_credentials(cam_id) or {}
    try:
        backend = select_backend(
            cam,
            creds,
            cloud_settings=_resolve_cloud_settings(),
            cloud_fallback_enabled=_cloud_fallback_enabled(),
        )
        await backend.login()
    except NoBackendAvailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"Backend-Login fehlgeschlagen: {e}")
    _backend_cache[cam_id] = backend
    return backend


def _invalidate_backend(cam_id: int) -> None:
    backend = _backend_cache.pop(cam_id, None)
    if backend is not None:
        # close ist async, wir lassen es laufen ohne await (Cleanup-Best-Effort)
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(backend.close())
        except RuntimeError:
            pass


# ============================================================================
# Health + Status (spezifisch vor /{id})
# ============================================================================


@router.get("/health")
async def health() -> dict[str, Any]:
    """Health-Check fuer das Modul. Pflicht-Endpoint im jarvis-admin-Lifecycle."""
    try:
        with connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM cameras").fetchone()["n"]
        db_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning("DB-Probe fehlgeschlagen: %s", e)
        count = 0
        db_ok = False

    backend_summary: dict[str, int] = {b: 0 for b in VALID_BACKENDS}
    if db_ok:
        for cam in list_cameras():
            b = cam.get("backend") or "tuya_lan"
            backend_summary[b] = backend_summary.get(b, 0) + 1

    return {
        "status": "ok" if db_ok else "degraded",
        "module": "jarnex-admin",
        "version": MODULE_VERSION,
        "db_ok": db_ok,
        "camera_count": count,
        "backends": backend_summary,
        "frigate_module_url": FRIGATE_MODULE_URL,
        "tuya_region_default": TUYA_REGION,
        "cloud_fallback_enabled": _cloud_fallback_enabled(),
    }


@router.get("/auth-context")
async def auth_context() -> dict[str, Any]:
    return {
        "module": "jarnex-admin",
        "auth_required": bool(ADMIN_API_KEY),
        "auth_scheme": "X-API-Key" if ADMIN_API_KEY else "none",
    }


# ============================================================================
# Discovery (spezifisch VOR /cameras/{id})
# ============================================================================


@router.post("/discover")
async def discover_route(payload: DiscoverRequest) -> dict[str, Any]:
    """TCP-Port-Probe-Discovery im CIDR. Klassifiziert Hosts:
      - tuya_lan      (Port 6668 offen)
      - tuya_with_rtsp (6668 + 554 + 8000)
      - rtsp          (554 + 8000 ohne 6668)
    """
    try:
        candidates = await jarnex_discovery.scan_subnet(
            payload.cidr,
            timeout=payload.timeout_s,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "cidr": payload.cidr,
        "candidates": candidates,
        "total": len(candidates),
        "jarnex_likely_count": sum(1 for c in candidates if c["jarnex_likely"]),
    }


@router.post("/discover/host")
async def discover_host_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Single-Host-Probe (Manual-Add-Workflow)."""
    host = (payload or {}).get("host")
    if not host or not isinstance(host, str):
        raise HTTPException(status_code=400, detail="body must be {'host': '<ip>'}")
    timeout = float((payload or {}).get("timeout_s", 1.5))
    result = await jarnex_discovery.probe_single_host(host, timeout=timeout)
    return {"host": host, "result": result}


# ============================================================================
# Cameras CRUD (spezifische Pfade vor /{cam_id})
# ============================================================================


@router.get("/cameras")
async def list_cameras_route(include_summary: bool = False) -> dict[str, Any]:
    cams = list_cameras()
    if include_summary:
        for cam in cams:
            cap_row = list_capabilities(cam["id"]) or {}
            summary_json = cap_row.get("summary")
            cam["summary"] = caps_mod.deserialize(summary_json)
    return {"cameras": cams, "total": len(cams)}


@router.post("/cameras")
async def create_camera_route(payload: CameraCreate) -> dict[str, Any]:
    try:
        cam_id = insert_camera(
            name=payload.name,
            host=payload.host,
            device_id=payload.device_id,
            port=payload.port,
            backend=payload.backend,
            stream_url=payload.stream_url,
            model=payload.model,
            firmware=payload.firmware,
            notes=payload.notes,
            local_key=payload.local_key,
            tuya_version=payload.tuya_version,
            rtsp_user=payload.rtsp_user,
            rtsp_pass=payload.rtsp_pass,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("create_camera failed: %s", e)
        raise HTTPException(status_code=400, detail=f"create_camera failed: {type(e).__name__}: {e}")
    cam = get_camera(cam_id) or {}
    return {"created": cam}


@router.get("/cameras/{cam_id}")
async def get_camera_route(cam_id: int) -> dict[str, Any]:
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    return cam


@router.patch("/cameras/{cam_id}")
async def update_camera_route(cam_id: int, payload: CameraUpdate) -> dict[str, Any]:
    if not get_camera(cam_id):
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        ok = update_camera(cam_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_backend(cam_id)
    return {"updated": ok, "camera": get_camera(cam_id)}


@router.delete("/cameras/{cam_id}")
async def delete_camera_route(cam_id: int) -> dict[str, Any]:
    if not get_camera(cam_id):
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    _invalidate_backend(cam_id)
    ok = delete_camera(cam_id)
    return {"deleted": ok}


@router.patch("/cameras/{cam_id}/credentials")
async def update_credentials_route(cam_id: int, payload: CredentialsUpdate) -> dict[str, Any]:
    if not get_camera(cam_id):
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    ok = update_credentials(
        cam_id,
        local_key=payload.local_key,
        tuya_version=payload.tuya_version,
        rtsp_user=payload.rtsp_user,
        rtsp_pass=payload.rtsp_pass,
    )
    _invalidate_backend(cam_id)
    return {"updated": ok}


# ============================================================================
# Capabilities
# ============================================================================


@router.get("/cameras/{cam_id}/capabilities")
async def capabilities_route(cam_id: int, refresh: bool = False) -> dict[str, Any]:
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")

    if not refresh:
        cached = get_capability(cam_id, "summary")
        if cached:
            summary = caps_mod.deserialize(cached)
            if summary:
                return {"cam_id": cam_id, "source": "cache", "summary": summary}

    backend = await _get_backend(cam_id)
    summary = await caps_mod.probe(backend)
    set_capability(cam_id, "summary", caps_mod.serialize(summary))

    if summary.get("stream_url"):
        update_camera(cam_id, stream_url=summary["stream_url"])

    return {"cam_id": cam_id, "source": "live", "summary": summary}


# ============================================================================
# Commands: PTZ, Light, Siren, Snapshot
# ============================================================================


@router.post("/cameras/{cam_id}/ptz")
async def ptz_route(cam_id: int, payload: PtzCommand) -> dict[str, Any]:
    backend = await _get_backend(cam_id)
    op = (payload.op or "").lower()

    # Bei op=stop: Cloud-RPC parallel als Hybrid-Backup. Live-verifiziert
    # 2026-05-29 auf Jarnex Ens-PL01: LAN-DP 151 ist Status-Mirror, kein
    # Edge-Trigger - Cloud-RPC ptz_stop=True ist robuster. Cloud feuert
    # fire-and-forget, User wartet nicht auf Cloud-Latenz (~400ms).
    if op == "stop" and backend.backend_id == "tuya_lan":
        cloud_settings = _resolve_cloud_settings()
        if cloud_settings:
            asyncio.create_task(_cloud_ptz_stop(cam_id, cloud_settings))

    try:
        result = await backend.ptz(payload.op, duration_s=payload.duration_s)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"PTZ fehlgeschlagen: {e}")

    # Update pan-offset tracking (Software-side Position-Estimate fuer Home-Position).
    # Left subtrahiert, Right addiert. Up/Down/Stop ignoriert (Pan-Only-Hardware).
    if op in ("left", "right") and payload.duration_s > 0:
        delta = -payload.duration_s if op == "left" else payload.duration_s
        try:
            current = float(get_setting(f"cam_{cam_id}_current_offset_s") or "0")
            set_setting(f"cam_{cam_id}_current_offset_s", str(current + delta))
        except (TypeError, ValueError):
            pass  # offset-tracking ist fail-soft, kein Show-Stopper

    return {"cam_id": cam_id, "op": payload.op, "result": result}


async def _cloud_ptz_stop(cam_id: int, cloud_settings: dict[str, str]) -> None:
    """Fire-and-forget Cloud-RPC ptz_stop. Exceptions geschluckt — Best-Effort."""
    try:
        from jarnex_backend import select_backend
        from jarnex_database import get_camera, get_credentials
        cam = get_camera(cam_id)
        if not cam:
            return
        cam_cloud = dict(cam)
        cam_cloud["backend"] = "tuya_cloud"
        cb = select_backend(
            cam_cloud,
            get_credentials(cam_id) or {},
            cloud_settings=cloud_settings,
            cloud_fallback_enabled=True,
        )
        await cb.login()
        await cb.ptz("stop", duration_s=0)
        await cb.close()
    except Exception:  # noqa: BLE001
        pass  # fire-and-forget; LAN-Stop ist primaerer Pfad


# ============================================================================
# Home-Position (Software-side Pan-Offset-Tracking)
# Pattern: Calibrate fahrt links bis Hardware-Anschlag -> Offset 0.
# Set-Home speichert aktuellen Offset als Home-Wert.
# Go-Home berechnet Differenz und fahrt entsprechende Direction/Duration.
# ============================================================================


@router.post("/cameras/{cam_id}/calibrate")
async def calibrate_route(cam_id: int) -> dict[str, Any]:
    """Faehrt Cam ganz nach links (10s) bis zum mechanischen Anschlag.
    Setzt current_offset = 0 und calibrated_at = now."""
    backend = await _get_backend(cam_id)
    try:
        await backend.ptz("left", duration_s=10.0)
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"calibrate fehlgeschlagen: {e}")
    set_setting(f"cam_{cam_id}_current_offset_s", "0.0")
    set_setting(f"cam_{cam_id}_calibrated_at", str(int(asyncio.get_event_loop().time())))
    return {
        "cam_id": cam_id,
        "current_offset_s": 0.0,
        "note": "Cam ist nun am linken Hardware-Anschlag (offset=0).",
    }


@router.post("/cameras/{cam_id}/home/set")
async def home_set_route(cam_id: int) -> dict[str, Any]:
    """Speichert aktuellen current_offset_s als home_offset_s."""
    if not get_camera(cam_id):
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    try:
        current = float(get_setting(f"cam_{cam_id}_current_offset_s") or "0")
    except (TypeError, ValueError):
        current = 0.0
    set_setting(f"cam_{cam_id}_home_offset_s", str(current))
    return {
        "cam_id": cam_id,
        "home_offset_s": current,
        "note": "Aktuelle Position als Home gespeichert.",
    }


@router.post("/cameras/{cam_id}/home/go")
async def home_go_route(cam_id: int) -> dict[str, Any]:
    """Faehrt Cam zur gespeicherten Home-Position. Strategie:
    1. Calibrate (links-Anschlag) -> reset offset auf 0
    2. Right fuer home_offset_s Sekunden -> Cam auf Home
    """
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    home_s = get_setting(f"cam_{cam_id}_home_offset_s")
    if not home_s:
        raise HTTPException(
            status_code=400,
            detail="kein Home gesetzt. Zuerst /home/set aufrufen (Cam manuell zu Home-Pos drehen).",
        )
    try:
        home_offset = float(home_s)
    except ValueError:
        raise HTTPException(status_code=500, detail=f"home_offset_s invalid: {home_s!r}")

    backend = await _get_backend(cam_id)
    # 1. Calibrate (10s left)
    try:
        await backend.ptz("left", duration_s=10.0)
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"home-go calibrate fehlgeschlagen: {e}")
    # 2. Right fuer home_offset_s
    if home_offset > 0:
        try:
            await backend.ptz("right", duration_s=min(home_offset, 10.0))
        except JarnexError as e:
            raise HTTPException(status_code=502, detail=f"home-go right fehlgeschlagen: {e}")
    set_setting(f"cam_{cam_id}_current_offset_s", str(home_offset))
    return {
        "cam_id": cam_id,
        "home_offset_s": home_offset,
        "note": "Cam an Home-Position gefahren.",
    }


@router.get("/cameras/{cam_id}/home")
async def home_status_route(cam_id: int) -> dict[str, Any]:
    """Status der Home-Position + Calibration."""
    if not get_camera(cam_id):
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    try:
        current = float(get_setting(f"cam_{cam_id}_current_offset_s") or "0")
    except (TypeError, ValueError):
        current = 0.0
    home_raw = get_setting(f"cam_{cam_id}_home_offset_s")
    home = float(home_raw) if home_raw else None
    cal = get_setting(f"cam_{cam_id}_calibrated_at")
    return {
        "cam_id": cam_id,
        "current_offset_s": current,
        "home_offset_s": home,
        "calibrated_at": int(cal) if cal else None,
        "is_calibrated": cal is not None,
        "is_home_set": home is not None,
    }


@router.post("/cameras/{cam_id}/light")
async def light_route(cam_id: int, payload: LightCommand) -> dict[str, Any]:
    backend = await _get_backend(cam_id)
    try:
        result = await backend.set_light(on=payload.on, brightness=payload.brightness)
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"set_light fehlgeschlagen: {e}")
    return {"cam_id": cam_id, "on": payload.on, "result": result}


@router.post("/cameras/{cam_id}/siren")
async def siren_route(cam_id: int, payload: SirenCommand) -> dict[str, Any]:
    """Sirenen-Trigger.

    WICHTIG (live-verifiziert 2026-05-29 mit Jarnex Ens-PL01): Tuya-LAN-DP-Set
    von `siren_switch` (DP 134) ist nur Armed-Toggle, KEIN Sound-Trigger. Echter
    Sound geht NUR ueber Tuya-Cloud-RPC mit demselben Code-Name. Daher:

    - Bei tuya_lan-Backend + Cloud-Settings hinterlegt: hybrid-dispatch — LAN
      setzt armed-state, Cloud-RPC triggert Sound.
    - Bei tuya_lan ohne Cloud-Settings: nur armed-toggle, kein Sound (HTTP 200
      mit Warning-Field). Caller muss Cloud-Backend setup'pen.
    - Bei tuya_cloud-Backend: direkter Cloud-Trigger, klingt.
    - Bei rtsp-Backend: NotImplementedError (kein Standard-ONVIF-Siren-Profil).
    """
    if payload.action != "play":
        raise HTTPException(status_code=400, detail="action muss 'play' sein")
    backend = await _get_backend(cam_id)

    result: dict[str, Any] = {}
    warnings: list[str] = []

    # Phase 1: backend-natives trigger_siren (LAN: armed-toggle, Cloud: sound)
    try:
        result["primary"] = await backend.trigger_siren()
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"trigger_siren fehlgeschlagen: {e}")

    # Phase 2: bei tuya_lan-Backend zusaetzlich Cloud-RPC fuer echten Sound
    if backend.backend_id == "tuya_lan":
        cloud_settings = _resolve_cloud_settings()
        if cloud_settings:
            cam = get_camera(cam_id)
            try:
                from jarnex_backend import select_backend
                from jarnex_database import get_credentials
                cam_cloud = dict(cam)
                cam_cloud["backend"] = "tuya_cloud"
                cloud_backend = select_backend(
                    cam_cloud,
                    get_credentials(cam_id) or {},
                    cloud_settings=cloud_settings,
                    cloud_fallback_enabled=True,
                )
                await cloud_backend.login()
                result["cloud_sound"] = await cloud_backend.trigger_siren()
                await cloud_backend.close()
            except (JarnexError, Exception) as e:  # noqa: BLE001
                warnings.append(f"Cloud-RPC fuer Sound-Trigger fehlgeschlagen: {e}")
        else:
            warnings.append(
                "Sirene wurde nur armed-toggled (kein Sound). Fuer echten Sound "
                "tuya_cloud_access_id/access_key/region in /settings hinterlegen."
            )

    return {
        "cam_id": cam_id,
        "action": "play",
        "result": result,
        "warnings": warnings,
    }


@router.get("/cameras/{cam_id}/snapshot")
async def snapshot_route(cam_id: int) -> Response:
    backend = await _get_backend(cam_id)
    try:
        jpeg = await backend.get_snapshot()
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"snapshot fehlgeschlagen: {e}")
    return Response(content=jpeg, media_type="image/jpeg")


@router.get("/cameras/{cam_id}/state")
async def state_route(cam_id: int) -> dict[str, Any]:
    backend = await _get_backend(cam_id)
    try:
        state = await backend.get_state()
    except JarnexError as e:
        raise HTTPException(status_code=502, detail=f"get_state fehlgeschlagen: {e}")
    return {"cam_id": cam_id, "backend": backend.backend_id, "state": state}


# ============================================================================
# Frigate-Bridge
# ============================================================================


@router.post("/cameras/{cam_id}/provision-to-frigate")
async def provision_to_frigate_route(cam_id: int) -> dict[str, Any]:
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    creds = get_credentials(cam_id) or {}

    snapshot_url: Optional[str] = None
    if not cam.get("stream_url"):
        # Snapshot-Polling-Fallback nur wenn Backend get_snapshot kann (Cloud / RTSP)
        backend_id = cam.get("backend") or "tuya_lan"
        if backend_id in ("tuya_cloud", "rtsp"):
            snapshot_url = (
                os.getenv("JARNEX_SELF_BASE_URL", "http://localhost:8403")
                + f"/modules/jarnex-admin/api/cameras/{cam_id}/snapshot"
            )

    try:
        payload = frigate_bridge.build_provision_payload(
            name=cam["name"],
            host=cam["host"],
            stream_url=cam.get("stream_url"),
            rtsp_user=creds.get("rtsp_user"),
            rtsp_pass=creds.get("rtsp_pass"),
            snapshot_url=snapshot_url,
            onvif_port=cam.get("port") if cam.get("backend") == "rtsp" else 8000,
        )
    except frigate_bridge.FrigateProvisionError as e:
        raise HTTPException(status_code=412, detail=str(e))

    try:
        result = await frigate_bridge.post_to_frigate(
            frigate_module_url=FRIGATE_MODULE_URL,
            admin_api_key=ADMIN_API_KEY or None,
            payload=payload,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "cam_id": cam_id,
        "frigate_module_url": FRIGATE_MODULE_URL,
        "mode": payload.get("_meta", {}).get("mode"),
        "frigate_response": result,
    }


@router.get("/cameras/{cam_id}/provision-preview")
async def provision_preview_route(cam_id: int) -> dict[str, Any]:
    cam = get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"camera id={cam_id} not found")
    creds = get_credentials(cam_id) or {}

    snapshot_url: Optional[str] = None
    if not cam.get("stream_url") and (cam.get("backend") or "") in ("tuya_cloud", "rtsp"):
        snapshot_url = (
            os.getenv("JARNEX_SELF_BASE_URL", "http://localhost:8403")
            + f"/modules/jarnex-admin/api/cameras/{cam_id}/snapshot"
        )

    try:
        payload = frigate_bridge.build_provision_payload(
            name=cam["name"],
            host=cam["host"],
            stream_url=cam.get("stream_url"),
            rtsp_user=creds.get("rtsp_user"),
            rtsp_pass=creds.get("rtsp_pass"),
            snapshot_url=snapshot_url,
            onvif_port=cam.get("port") if cam.get("backend") == "rtsp" else 8000,
        )
    except frigate_bridge.FrigateProvisionError as e:
        return {"cam_id": cam_id, "can_provision": False, "reason": str(e)}

    return {
        "cam_id": cam_id,
        "can_provision": True,
        "preview": frigate_bridge.redact_payload(payload),
    }


# ============================================================================
# Settings KV
# ============================================================================


@router.get("/settings/{key}")
async def get_setting_route(key: str) -> dict[str, Any]:
    return {"key": key, "value": get_setting(key)}


@router.put("/settings/{key}")
async def set_setting_route(key: str, value: dict[str, Any]) -> dict[str, Any]:
    str_value = value.get("value")
    if not isinstance(str_value, str):
        raise HTTPException(status_code=400, detail="body must be {'value': '<string>'}")
    set_setting(key, str_value)
    return {"key": key, "value": str_value}


# ============================================================================
# Alarm-Listener Lifecycle
# ============================================================================


@router.post("/alarms/start")
async def alarms_start() -> dict[str, Any]:
    return await alarm_mod.get_listener().start()


@router.post("/alarms/stop")
async def alarms_stop() -> dict[str, Any]:
    return await alarm_mod.get_listener().stop()


@router.get("/alarms/status")
async def alarms_status() -> dict[str, Any]:
    return alarm_mod.get_listener().stats


@router.get("/alarms/recent")
async def alarms_recent(limit: int = 20, cam_id: Optional[int] = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    events = list_events(cam_id=cam_id, limit=limit)
    return {"events": events, "count": len(events)}
