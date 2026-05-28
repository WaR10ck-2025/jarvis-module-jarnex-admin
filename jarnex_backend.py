"""
jarnex_backend.py - Backend-Adapter-Protocol fuer die 3 moeglichen Cam-Backends.

Da Jarnex-Cams (Tuya-OEM) drei moegliche Kommunikations-Wege haben, abstrahiert
dieses Modul den Client-Layer hinter einem Protocol. Der Modul-Selector
(`select_backend`) waehlt zur Laufzeit anhand des cam_row + Settings.

Backends:
  - JarnexTuyaLAN   - Default. tinytuya, DP-Map, TCP 6668, kein Stream
  - JarnexTuyaCloud - Auto-Fallback nach 3 LAN-Errors mit 5min Cooldown
  - JarnexRTSP      - Aktiv wenn cam_row.stream_url gesetzt (Stock-ONVIF oder Post-OpenIPC)

Auto-Switch-Pattern (im Alarm-Listener implementiert):
  - LAN-Backend zaehlt consecutive errors -> nach 3 wechselt zu Cloud
  - Cloud-Backend hat Cooldown 300s; nach Ablauf wird LAN-Re-Try probiert
  - Events kommen mit source_backend-Label in die DB
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("jarvis.module.jarnex_admin.backend")


class JarnexError(Exception):
    """Backend-agnostische Basis-Exception."""


class JarnexAuthError(JarnexError):
    """Login/Token/Local-Key-Fehlschlag."""


class JarnexUnreachable(JarnexError):
    """Cam nicht erreichbar (TCP-Timeout, UDP-Loss, DNS, etc.)."""


class NoBackendAvailable(JarnexError):
    """Weder local_key noch stream_url verfuegbar - Cam nicht ansprechbar."""


@dataclass
class JarnexEvent:
    """Backend-agnostisches Event-Format. Wird in DB events serialisiert."""
    label: str
    score: float | None
    raw: dict[str, Any] = field(default_factory=dict)
    source_backend: str = "tuya_lan"


@runtime_checkable
class JarnexBackend(Protocol):
    """Common Interface aller 3 Backend-Implementierungen.

    Implementierungen erlauben fail-soft: einzelne Methoden duerfen
    NotImplementedError werfen (z.B. RTSP-Backend kann kein Siren).
    """

    backend_id: str  # "tuya_lan" | "tuya_cloud" | "rtsp"

    async def login(self) -> None:
        """Authentifizierung + Verbindungs-Setup. Idempotent."""
        ...

    async def close(self) -> None:
        """Verbindung schliessen, Tasks aufraeumen."""
        ...

    async def get_state(self) -> dict[str, Any]:
        """Aktueller State (light_on, brightness, pan, tilt, motion_armed)."""
        ...

    async def set_light(self, on: bool, brightness: int | None = None) -> dict[str, Any]:
        ...

    async def ptz(self, op: str, duration_s: float = 1.0) -> dict[str, Any]:
        """op: Left/Right/Up/Down/Stop. Speed/Preset optional."""
        ...

    async def trigger_siren(self) -> dict[str, Any]:
        ...

    async def get_snapshot(self) -> bytes:
        """JPEG bytes."""
        ...

    async def poll_events(self) -> list[JarnexEvent]:
        """Edge-triggered events seit letztem Poll."""
        ...

    def get_stream_url(self) -> str | None:
        """Sync. None wenn kein Stream verfuegbar."""
        ...


def select_backend(
    cam_row: dict[str, Any],
    credentials: dict[str, Any] | None,
    *,
    cloud_settings: dict[str, str] | None = None,
    cloud_fallback_enabled: bool = False,
) -> JarnexBackend:
    """Backend-Selector. Reine Funktion, nutzt cam_row.backend + stream_url.

    Args:
        cam_row: Zeile aus jarnex_database.get_camera()
        credentials: get_credentials()-Ergebnis (local_key, rtsp_user, etc.)
        cloud_settings: Tuya-Cloud-Settings (project_id, region, access_id, access_key)
        cloud_fallback_enabled: globales Flag aus Settings

    Returns:
        Eine der 3 Backend-Klassen, instanziert mit cam_row + credentials.

    Raises:
        NoBackendAvailable: weder local_key noch stream_url noch RTSP-Creds.
    """
    backend_id = (cam_row.get("backend") or "tuya_lan").lower()
    stream_url = cam_row.get("stream_url")
    credentials = credentials or {}

    if backend_id == "rtsp" or stream_url:
        # Lazy-Import gegen sys.modules-Collision
        try:
            from . import jarnex_rtsp  # type: ignore
        except ImportError:
            import jarnex_rtsp  # type: ignore
        if not stream_url:
            raise NoBackendAvailable(
                f"cam {cam_row.get('id')}: backend=rtsp aber stream_url leer"
            )
        return jarnex_rtsp.JarnexRTSP(
            host=cam_row["host"],
            stream_url=stream_url,
            rtsp_user=credentials.get("rtsp_user"),
            rtsp_pass=credentials.get("rtsp_pass"),
            onvif_port=cam_row.get("port") or 8000,
        )

    if backend_id == "tuya_cloud":
        try:
            from . import jarnex_tuya_cloud  # type: ignore
        except ImportError:
            import jarnex_tuya_cloud  # type: ignore
        if not cloud_settings:
            raise NoBackendAvailable(
                f"cam {cam_row.get('id')}: backend=tuya_cloud aber keine cloud_settings"
            )
        return jarnex_tuya_cloud.JarnexTuyaCloud(
            device_id=cam_row.get("device_id") or "",
            project_id=cloud_settings.get("tuya_cloud_project_id", ""),
            region=cloud_settings.get("tuya_cloud_region", "eu"),
            access_id=cloud_settings.get("tuya_cloud_access_id", ""),
            access_key=cloud_settings.get("tuya_cloud_access_key", ""),
        )

    # Default: tuya_lan
    try:
        from . import jarnex_tuya_lan  # type: ignore
    except ImportError:
        import jarnex_tuya_lan  # type: ignore
    local_key = credentials.get("local_key")
    if not local_key:
        raise NoBackendAvailable(
            f"cam {cam_row.get('id')}: backend=tuya_lan aber kein local_key in credentials"
        )
    return jarnex_tuya_lan.JarnexTuyaLAN(
        device_id=cam_row.get("device_id") or "",
        host=cam_row["host"],
        local_key=local_key,
        port=cam_row.get("port") or 6668,
        version=credentials.get("tuya_version") or "3.3",
        cloud_fallback_enabled=cloud_fallback_enabled,
    )
