"""
jarnex_tuya_lan.py - Backend 1: Tuya-LAN-Wrapper (Default).

Spricht Tuya-LAN-Protocol auf TCP 6668. Nutzt tinytuya wenn installiert,
sonst fail-soft. Fuer Test-Isolation kann ein `transport` injiziert werden,
das tinytuya nachahmt.

DP-Map fuer Jarnex JNOL4 (zu validieren beim ersten Pairing):
  - DP 1   bool   Light Switch on/off
  - DP 22  int    Brightness 0-100
  - DP 101 enum   PTZ Direction (up/down/left/right/stop)
  - DP 102 bool   Motion Detection enabled
  - DP 103 bool   AI Person Detection enabled
  - DP 104 bool   Siren Trigger
  - DP 109 bool   Motion Event (poll, edge-triggered)
  - DP 110 bool   AI Person Event (poll, edge-triggered)

Die Map wird in cam.capabilities[dp_id_map] gecached und ist pro Cam editierbar
(verschiedene OEM-Firmware-Versionen haben unterschiedliche DP-IDs).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional, Protocol

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

logger = logging.getLogger("jarvis.module.jarnex_admin.tuya_lan")


DEFAULT_DP_MAP: dict[str, int] = {
    "light_switch": 1,
    "brightness": 22,
    "ptz_direction": 101,
    "motion_armed": 102,
    "ai_person_armed": 103,
    "siren_trigger": 104,
    "motion_event": 109,
    "ai_person_event": 110,
    "snapshot_trigger": 132,
}


class TuyaTransport(Protocol):
    """Minimal-Interface fuer den Tuya-LAN-Transport.

    Erlaubt Test-Injection eines Fake-Transports ohne tinytuya zu mocken.
    Im Production-Path wraps `tinytuya.OutletDevice` / `tinytuya.Device`.
    """

    async def status(self) -> dict[str, Any]:
        """Returns {"dps": {dp_id: value, ...}} - rohes Tuya-Response."""
        ...

    async def set_value(self, dp_id: int, value: Any) -> dict[str, Any]:
        ...


class _TinyTuyaTransport:
    """Adapter um tinytuya.Device.

    tinytuya ist sync, wir wrappen in run_in_executor damit der Event-Loop
    nicht blockiert. Wird lazy importiert (tinytuya nicht in Test-Env Pflicht).
    """

    def __init__(self, device_id: str, host: str, local_key: str, version: str = "3.3"):
        try:
            import tinytuya  # type: ignore
        except ImportError as e:
            raise JarnexError(
                "tinytuya nicht installiert. `pip install tinytuya` "
                "oder im Test einen TuyaTransport injizieren."
            ) from e
        self._dev = tinytuya.Device(device_id, host, local_key, version=float(version))

    async def status(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._dev.status)
        except (ConnectionError, OSError, TimeoutError) as e:
            raise JarnexUnreachable(f"tinytuya status() fehlgeschlagen: {e}") from e

    async def set_value(self, dp_id: int, value: Any) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._dev.set_value, dp_id, value)
        except (ConnectionError, OSError, TimeoutError) as e:
            raise JarnexUnreachable(f"tinytuya set_value() fehlgeschlagen: {e}") from e


class JarnexTuyaLAN:
    """Backend 1: Tuya-LAN. Nutzt TuyaTransport-Protocol fuer Testability."""

    backend_id = "tuya_lan"

    def __init__(
        self,
        *,
        device_id: str,
        host: str,
        local_key: str,
        port: int = 6668,
        version: str = "3.3",
        cloud_fallback_enabled: bool = False,
        dp_map: Optional[dict[str, int]] = None,
        transport: Optional[TuyaTransport] = None,
    ):
        self.device_id = device_id
        self.host = host
        self.local_key = local_key
        self.port = port
        self.version = version
        self.cloud_fallback_enabled = cloud_fallback_enabled
        self.dp_map = dict(DEFAULT_DP_MAP)
        if dp_map:
            self.dp_map.update(dp_map)
        self._transport = transport
        self._last_event_state: dict[str, bool] = {}

    def _ensure_transport(self) -> TuyaTransport:
        if self._transport is None:
            self._transport = _TinyTuyaTransport(
                self.device_id, self.host, self.local_key, self.version
            )
        return self._transport

    def _dp(self, label: str) -> int:
        dp_id = self.dp_map.get(label)
        if dp_id is None:
            raise JarnexError(f"DP-Map hat kein Mapping fuer {label!r}")
        return dp_id

    async def login(self) -> None:
        if not self.local_key:
            raise JarnexAuthError("local_key leer - Pairing noetig")
        # Tuya-LAN hat keinen expliziten Login-Step, der ersten Status-Call ist der Probe.
        try:
            await self._ensure_transport().status()
        except JarnexError:
            raise
        except Exception as e:  # noqa: BLE001
            raise JarnexAuthError(f"Tuya-LAN-Probe fehlgeschlagen: {e}") from e

    async def close(self) -> None:
        # tinytuya schliesst je Call. Nichts zu tun.
        self._transport = None

    async def get_state(self) -> dict[str, Any]:
        raw = await self._ensure_transport().status()
        dps = (raw or {}).get("dps") or {}
        return {
            "light_on": bool(dps.get(str(self._dp("light_switch")))),
            "brightness": dps.get(str(self._dp("brightness"))),
            "motion_armed": bool(dps.get(str(self._dp("motion_armed")))),
            "ai_person_armed": bool(dps.get(str(self._dp("ai_person_armed")))),
            "raw_dps": dps,
        }

    async def set_light(self, on: bool, brightness: int | None = None) -> dict[str, Any]:
        t = self._ensure_transport()
        r1 = await t.set_value(self._dp("light_switch"), bool(on))
        out: dict[str, Any] = {"switch": r1}
        if brightness is not None:
            brightness = max(0, min(100, int(brightness)))
            out["brightness"] = await t.set_value(self._dp("brightness"), brightness)
        return out

    async def ptz(self, op: str, duration_s: float = 1.0) -> dict[str, Any]:
        op_norm = (op or "").lower()
        if op_norm not in ("up", "down", "left", "right", "stop"):
            raise JarnexError(f"PTZ op muss up/down/left/right/stop sein, bekam {op!r}")
        t = self._ensure_transport()
        result = await t.set_value(self._dp("ptz_direction"), op_norm)
        if op_norm != "stop" and duration_s > 0:
            await asyncio.sleep(min(duration_s, 10.0))
            result_stop = await t.set_value(self._dp("ptz_direction"), "stop")
            return {"move": result, "stop": result_stop}
        return {"move": result}

    async def trigger_siren(self) -> dict[str, Any]:
        t = self._ensure_transport()
        r = await t.set_value(self._dp("siren_trigger"), True)
        return {"siren": r}

    async def get_snapshot(self) -> bytes:
        """Tuya-LAN bietet keinen direkten JPEG-Stream. Snapshot-DP triggert das
        Hochladen eines Frames in Tuya-Cloud-Storage - lokal ist es nicht
        zugaenglich. Wir liefern hier explizit einen Fehler, damit der Caller
        weiss, dass er ein anderes Backend nutzen muss.
        """
        raise JarnexError(
            "Tuya-LAN-Backend kann kein Snapshot lokal liefern. "
            "Stream-URL setzen (Stock-ONVIF oder OpenIPC) oder Cloud-Backend nutzen."
        )

    async def poll_events(self) -> list[JarnexEvent]:
        """Edge-Trigger auf motion_event und ai_person_event DPs."""
        raw = await self._ensure_transport().status()
        dps = (raw or {}).get("dps") or {}
        events: list[JarnexEvent] = []
        for label, event_label in (
            ("motion_event", "motion"),
            ("ai_person_event", "ai_person"),
        ):
            try:
                dp_id = self._dp(label)
            except JarnexError:
                continue
            cur = bool(dps.get(str(dp_id)))
            prev = self._last_event_state.get(label, False)
            if cur and not prev:
                events.append(JarnexEvent(
                    label=event_label,
                    score=None,
                    raw={"dp_id": dp_id, "value": cur, "all_dps": dps},
                    source_backend=self.backend_id,
                ))
            self._last_event_state[label] = cur
        return events

    def get_stream_url(self) -> str | None:
        return None
