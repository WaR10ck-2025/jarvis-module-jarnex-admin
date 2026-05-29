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


# Tuya-Smart-Camera-Convention (live-verifiziert 2026-05-29 gegen Jarnex JNOL4/Ens-PL01).
# WICHTIG: Smart-Cams verwenden DPs 100+, NICHT die Smart-Plug-Defaults (DP 1, 22).
# Tuya-Standard-Instruction-Set ab DP 101:
#   basic_indicator(101), basic_private(103), basic_flip(104), basic_osd(105),
#   nightvision_mode(106), motion_switch(107), decibel_sensitivity(109),
#   sd_status(110), record_switch(116), ipc_sharp(117), ptz_control(119),
#   ipc_bright(123), nightvision_mode_alt(124), siren_switch(134),
#   floodlight_switch(138), humanoid_filter(139), motion_tracking(150),
#   ptz_stop(151), basic_wdr(159), basic_device_volume(160),
#   ipc_siren_duration(193), ipc_siren_volume(194), ipc_object_outline(197),
#   ipc_mute_record(198)
#
# Sirenen-Sub-System (live-verifiziert 2026-05-29):
#   DP 134 = siren_switch (Boolean)
#       - Armed-Master-Toggle: True = Auto-Sirene-bei-Motion erlaubt
#       - WICHTIG: KEIN direkter Sound-Trigger via LAN-DP-Set!
#       - LAN set(134, True) macht NIE Sound, nur state-toggle
#       - Sound-Trigger NUR via Cloud-RPC mit demselben Code-Name "siren_switch"
#       - Tuya-Architektur-Quirk: identischer Code, anderer Channel = anderes Behavior
#   DP 168 = ipc_auto_siren (Boolean)
#       - Auto-Sirene wirklich aktivieren bei Motion-/AI-Detection-Events
#       - Voraussetzung dass siren_switch=True + ipc_auto_siren=True
#   DP 193 = ipc_siren_duration (Integer 1-60s)
#       - Sirenen-Spielzeit bei Trigger (Default 50s, sehr lang!)
#       - Vor Test-Trigger auf 5s reduzieren um Laerm zu begrenzen
#   DP 194 = ipc_siren_volume (Integer 0-100%)
#       - Lautstaerke. Default ~5 (von Skala bis 100?), Cam ist trotzdem 100-110 dB
#   DP 160 = (vermutlich State-Mirror der Cloud-Aktion)
#       - Beim App-Sirenen-Trigger sah man 12 → 1 → 12 mit ~5s Pause
#       - Direkter LAN-Set 160=1 macht KEINE Sirene → State-only-Mirror, kein Trigger
DEFAULT_DP_MAP: dict[str, int] = {
    "light_switch": 138,         # floodlight_switch (Boolean)
    "brightness": 123,           # ipc_bright (Integer, IPC-Image-Brightness; Lampe selbst hat keine eigene Brightness)
    "ptz_direction": 119,        # ptz_control (Enum: "0"-"7" fuer up/down/left/right/etc.)
    "ptz_stop": 151,             # ptz_stop (Boolean toggle)
    "motion_armed": 107,         # motion_switch (Boolean)
    "ai_person_armed": 139,      # humanoid_filter (Boolean, AI-Person-Filter)
    "siren_trigger": 134,        # siren_switch (Boolean)
    "indicator_led": 101,        # basic_indicator (Boolean)
    "private_mode": 103,         # basic_private (Boolean)
    "nightvision_mode": 106,     # nightvision_mode (Enum string "0"=auto, "1"=on, "2"=off)
    "motion_event": 115,         # motion event (typ. trigger DP)
    "ai_person_event": 117,      # AI-Person event
    "record_switch": 116,        # record_switch (Boolean)
}

# PTZ-Enum-Mapping fuer DP 119 (ptz_control).
#
# WICHTIG: Cam-Modell-spezifische Enum-Range — vor Production-Use Functions-API
# checken: GET /v1.0/iot-03/devices/{id}/functions -> ptz_control.values.range
#
# Standard-Tuya-Doku: 0=stop, 1=top, 2=bottom, 3=left, 4=right, 5=top_left,
#   6=top_right, 7=bottom_left, 8=bottom_right
#
# JARNEX Ens-PL01 (live-verifiziert 2026-05-29) hat eingeschraenkte Range:
#   ["1","2","3","5","6","7"] — KEINE "0", "4", "8"!
#   "Pan-Right" fehlt als reiner Wert, wird ueber "6" (top_right) approximiert.
#   Bei Pan-Only-Hardware wird die Tilt-Komponente ignoriert.
#
# Stop ueber dedizierten DP 151 (ptz_stop=True) statt ptz_control="0",
# da "0" nicht in der Range ist.
PTZ_ENUM_MAP: dict[str, str] = {
    "stop": "0",     # fallback, normal via ptz_stop DP 151
    "up": "1",       # = top
    "down": "2",     # = bottom
    "left": "3",     # = left (live verifiziert)
    "right": "6",    # = top_right (live verifiziert: "4" wird abgelehnt, "6" arbeitet)
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
        if op_norm not in PTZ_ENUM_MAP:
            raise JarnexError(
                f"PTZ op muss {list(PTZ_ENUM_MAP)} sein, bekam {op!r}"
            )
        t = self._ensure_transport()
        # Tuya-Smart-Cam ptz_control ist Enum-String, NICHT Direction-Name.
        # PTZ_ENUM_MAP mappt 'left' -> '3' usw. (Tuya-Standard-Instruction-Set).
        if op_norm == "stop":
            # Defensiv beide DPs setzen: manche Cams reagieren nur auf ptz_stop-Toggle.
            result = await t.set_value(self._dp("ptz_direction"), PTZ_ENUM_MAP["stop"])
            try:
                result_stop = await t.set_value(self._dp("ptz_stop"), True)
                return {"stop": result_stop, "direction_zero": result}
            except JarnexError:
                return {"direction_zero": result}
        result = await t.set_value(self._dp("ptz_direction"), PTZ_ENUM_MAP[op_norm])
        if duration_s > 0:
            await asyncio.sleep(min(duration_s, 10.0))
            # Stop ueber dedizierten ptz_stop-Toggle (Boolean), nicht ptz_control=0
            try:
                result_stop = await t.set_value(self._dp("ptz_stop"), True)
            except JarnexError:
                # Fallback: ptz_control = "0" (stop)
                result_stop = await t.set_value(self._dp("ptz_direction"), PTZ_ENUM_MAP["stop"])
            return {"move": result, "stop": result_stop}
        return {"move": result}

    async def trigger_siren(self) -> dict[str, Any]:
        """Setzt siren_switch (DP 134) auf True.

        WICHTIG (live-verifiziert 2026-05-29 mit Jarnex Ens-PL01):
        siren_switch ist KEIN direkter Sound-Trigger. Es ist ein
        Armed-Toggle fuer Auto-Sirene bei Motion-Events. Setzen auf
        True macht Cam fuer 'spiele Sirene bei naechstem Motion-Event'
        bereit. Echter Sound kommt nur via Auto-Trigger (Motion +
        ipc_auto_siren=True auf DP 168).

        Manueller Sound-Test: False -> sleep 1s -> True provoziert auf
        manchen Cams einen Transition-Trigger, aber NICHT auf der
        getesteten Jarnex. Tuya-Smart-Cams haben in Standard-Instruction-
        Set keinen manuellen "play siren now"-DP.

        Fuer echten Lärm-Test: ipc_auto_siren auf DP 168 = True setzen
        UND Motion provozieren (Hand vor Cam wedeln).
        """
        t = self._ensure_transport()
        r = await t.set_value(self._dp("siren_trigger"), True)
        return {"siren_armed": r, "note": "armed-toggle, kein direkter Sound-Trigger"}

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
