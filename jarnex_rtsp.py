"""
jarnex_rtsp.py - Backend 2: ONVIF/RTSP (Post-OpenIPC oder Stock-ONVIF).

Aktiv wenn cam_row.stream_url gesetzt ist. Implementiert das volle
JarnexBackend-Protocol mit ONVIF/RTSP-Standard. Frigate-Provision ist
ohne Einschraenkung moeglich.

Note: Wir nutzen httpx fuer Snapshot-via-ONVIF-Media-Service. ONVIF-PTZ-Calls
wuerden eigentlich `onvif-zeep-async` brauchen - in Phase 1 sind diese als
NotImplementedError geparked, damit der Test-Layer ohne Zeep gruen wird.
Phase 6 fuellt das aus, wenn die Hardware tatsaechlich diesen Pfad nutzt.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from .jarnex_backend import (
        JarnexAuthError,
        JarnexBackend,
        JarnexError,
        JarnexEvent,
        JarnexUnreachable,
    )
except ImportError:
    from jarnex_backend import (  # type: ignore
        JarnexAuthError,
        JarnexBackend,
        JarnexError,
        JarnexEvent,
        JarnexUnreachable,
    )

logger = logging.getLogger("jarvis.module.jarnex_admin.rtsp")


class JarnexRTSP:
    """Backend 2: ONVIF/RTSP. Phase-1 minimal-set fuer Frigate-Provision-Path."""

    backend_id = "rtsp"

    def __init__(
        self,
        *,
        host: str,
        stream_url: str,
        rtsp_user: Optional[str] = None,
        rtsp_pass: Optional[str] = None,
        onvif_port: int = 8000,
        transport: Any = None,
    ):
        self.host = host
        self.stream_url = stream_url
        self.rtsp_user = rtsp_user or ""
        self.rtsp_pass = rtsp_pass or ""
        self.onvif_port = onvif_port
        self._transport = transport
        self._client = None
        self._logged_in = False

    def _get_client(self):
        if self._client is None:
            try:
                import httpx  # type: ignore
            except ImportError as e:
                raise JarnexError("httpx nicht installiert") from e
            kwargs: dict[str, Any] = {"timeout": 5.0}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def login(self) -> None:
        # ONVIF braucht WSSE-UsernameToken im SOAP-Body, kein Session-Login.
        if not self.stream_url:
            raise JarnexAuthError("stream_url leer")
        self._logged_in = True

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
        self._logged_in = False

    async def get_state(self) -> dict[str, Any]:
        """Phase-1 Stub: liefert nur die Stream-URL.
        Phase 6 wird ONVIF DeviceInfo + Imaging-Settings ergaenzen."""
        return {
            "stream_url": self.stream_url,
            "host": self.host,
            "onvif_port": self.onvif_port,
        }

    async def set_light(self, on: bool, brightness: int | None = None) -> dict[str, Any]:
        # ONVIF hat keine Standard-Lampensteuerung (Imaging-Service hat ggf. IR-Cut/IR-Mode).
        # Vermutlich braucht es Tuya-Cloud-Backend parallel fuer Light - ist OK.
        raise JarnexError(
            "RTSP-Backend kann nicht direkt das Tuya-Lampen-Modul steuern. "
            "Tuya-LAN-Backend fuer Light/PTZ parallel nutzen oder OpenIPC mit MQTT-Bridge konfigurieren."
        )

    async def ptz(self, op: str, duration_s: float = 1.0) -> dict[str, Any]:
        raise NotImplementedError(
            "ONVIF-PTZ Phase-6: braucht onvif-zeep-async. Aktuell nicht implementiert."
        )

    async def trigger_siren(self) -> dict[str, Any]:
        raise JarnexError("RTSP-Backend hat keinen Siren-Trigger (kein ONVIF-Standard).")

    async def get_snapshot(self) -> bytes:
        """ONVIF Media-Service liefert Snapshot-URI ueber GetSnapshotUri. Phase-1
        nutzt einen direkten HTTP-GET auf den (typischen) Stock-Snapshot-Pfad.
        Wenn das nicht klappt, Phase 6 implementiert echtes ONVIF GetSnapshotUri.
        """
        client = self._get_client()
        # Heuristik: viele ONVIF-Cams exposen /onvif/snapshot oder /image.jpg
        candidate_urls = [
            f"http://{self.host}/onvif/snapshot",
            f"http://{self.host}/image.jpg",
        ]
        auth = (self.rtsp_user, self.rtsp_pass) if self.rtsp_user else None
        last_err: Optional[Exception] = None
        for url in candidate_urls:
            try:
                resp = await client.get(url, auth=auth)
                if resp.status_code == 200 and resp.content:
                    return resp.content
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        raise JarnexError(
            f"Kein Snapshot-Pfad erreichbar (probiert {candidate_urls}): {last_err}"
        )

    async def poll_events(self) -> list[JarnexEvent]:
        # ONVIF-Events kommen via PullPoint-Subscription. Phase-1 leer.
        return []

    def get_stream_url(self) -> str | None:
        """RTSP-URL mit URL-encoded Credentials, falls vorhanden.

        Wichtig: Reolink-Memory `feedback_frigate_go2rtc_url_encoded_rtsp_password.md`
        - bei Sonderzeichen in PW MUSS encoded werden, sonst lehnt go2rtc die URL ab.
        """
        if not self.stream_url:
            return None
        if not self.rtsp_user or "@" in self.stream_url:
            return self.stream_url
        from urllib.parse import quote
        user = quote(self.rtsp_user, safe="")
        pwd = quote(self.rtsp_pass, safe="")
        if self.stream_url.startswith("rtsp://"):
            rest = self.stream_url[len("rtsp://"):]
            return f"rtsp://{user}:{pwd}@{rest}"
        return self.stream_url
