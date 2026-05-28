"""
jarnex_capabilities.py - Capability-Probe und DP-Map-Discovery pro Cam.

Ruft das aktive Backend ab und baut einen Summary-Block, der in den DB-Cache
geschrieben wird. Folge-Calls (z.B. Provision-Frigate) lesen aus dem Cache,
ohne weitere Live-Calls.

Summary-Schema:
{
  "backend_id": "tuya_lan" | "tuya_cloud" | "rtsp",
  "has_light": bool,
  "has_ptz": bool,
  "has_siren": bool,
  "has_ai_person": bool,
  "has_motion": bool,
  "has_snapshot_local": bool,
  "stream_url": str | None,
  "dp_id_map": dict[str, int] | None,    # nur fuer tuya_lan
  "model": str | None,
  "firmware": str | None,
  "probed_at": int,
}
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

try:
    from .jarnex_backend import JarnexBackend, JarnexError
except ImportError:
    from jarnex_backend import JarnexBackend, JarnexError  # type: ignore

logger = logging.getLogger("jarvis.module.jarnex_admin.capabilities")


async def probe(backend: JarnexBackend) -> dict[str, Any]:
    """Live-Probe gegen das Backend. Best-effort - fehlende Caps sind kein Fehler.

    Returns:
        Capability-Summary-Dict (JSON-serialisierbar).
    """
    summary: dict[str, Any] = {
        "backend_id": backend.backend_id,
        "has_light": False,
        "has_ptz": False,
        "has_siren": False,
        "has_ai_person": False,
        "has_motion": False,
        "has_snapshot_local": False,
        "stream_url": None,
        "dp_id_map": None,
        "model": None,
        "firmware": None,
        "probed_at": int(time.time()),
    }

    # Stream-URL: Sync-Methode aller Backends. Wenn nicht None, hat das Backend RTSP.
    try:
        summary["stream_url"] = backend.get_stream_url()
    except Exception as e:  # noqa: BLE001
        logger.debug("get_stream_url Probe fail: %s", e)

    # Backend-spezifische Hints (statisch, damit Probe ohne Live-Call deterministisch ist)
    if backend.backend_id == "tuya_lan":
        summary["has_light"] = True
        summary["has_ptz"] = True
        summary["has_siren"] = True
        summary["has_ai_person"] = True
        summary["has_motion"] = True
        summary["has_snapshot_local"] = False
        if hasattr(backend, "dp_map"):
            summary["dp_id_map"] = dict(backend.dp_map)
    elif backend.backend_id == "tuya_cloud":
        summary["has_light"] = True
        summary["has_ptz"] = True
        summary["has_siren"] = True
        summary["has_ai_person"] = True
        summary["has_motion"] = True
        summary["has_snapshot_local"] = True  # Cloud kann Snapshot via /picture
    elif backend.backend_id == "rtsp":
        summary["has_light"] = False
        summary["has_ptz"] = False  # Phase-1 NotImplemented
        summary["has_siren"] = False
        summary["has_motion"] = False
        summary["has_ai_person"] = False
        summary["has_snapshot_local"] = True  # ONVIF Snapshot-URI

    # Live-State-Probe. Backend-spezifischer Payload-Validation-Check —
    # NICHT nur isinstance(state, dict)! tinytuya liefert bei falschem
    # local_key ein dict mit leerem raw_dps statt Exception (siehe
    # feedback_tuya_cam_substrate_fingerprint.md Punkt 5).
    try:
        state = await backend.get_state()
        summary["live_probe_ok"] = _is_live(backend.backend_id, state)
    except Exception as e:  # noqa: BLE001
        logger.info("get_state Probe fail (toleriert): %s", e)
        summary["live_probe_ok"] = False

    return summary


def _is_live(backend_id: str, state: Any) -> bool:
    """Backend-spezifischer Payload-Validation-Check.

    Reachability (TCP-Connect, HTTP-200) ist NICHT gleich Liveness:
    - tuya_lan: falscher local_key liefert leeres raw_dps statt Error
    - tuya_cloud: invalid token liefert leeres raw_codes statt Error
    - rtsp: stream_url ist statisch, also Liveness = stream_url-Wahrheit
    """
    if not isinstance(state, dict):
        return False
    if backend_id == "tuya_lan":
        return bool(state.get("raw_dps"))
    if backend_id == "tuya_cloud":
        return bool(state.get("raw_codes"))
    if backend_id == "rtsp":
        return bool(state.get("stream_url"))
    return False


def serialize(summary: dict[str, Any]) -> str:
    """JSON-Serialisierung mit stabiler Key-Reihenfolge."""
    return json.dumps(summary, sort_keys=True)


def deserialize(payload: str | None) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return None
