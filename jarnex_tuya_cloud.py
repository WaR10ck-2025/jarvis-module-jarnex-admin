"""
jarnex_tuya_cloud.py - Backend 3: Tuya-IoT-Cloud-API (Event-Fallback).

Aktiv wenn LAN-Backend nach 3 Errors mit 5min Cooldown nicht erreichbar war.
Erwartet ein registriertes Tuya-IoT-Project (developer.tuya.com) mit:
  - project_id, access_id, access_key, region (eu/us/cn/in)

Implementiert das selbe JarnexBackend-Protocol wie LAN/RTSP, damit der Caller
agnostisch bleibt. HTTP-Transport ueber httpx (async). Token wird in-memory
gecached (TTL ~2h).

Hinweis: Tuya-Cloud-API erlaubt Stream nur als M3U8/HLS-URL mit Token, das
periodisch refreshed werden muss. Wir liefern den HLS-URL via get_stream_url(),
aber das ist KEIN echtes RTSP - Frigate-Provision braucht weitere Adaptation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
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

logger = logging.getLogger("jarvis.module.jarnex_admin.tuya_cloud")


REGION_HOSTS: dict[str, str] = {
    "eu": "openapi.tuyaeu.com",
    "us": "openapi.tuyaus.com",
    "cn": "openapi.tuyacn.com",
    "in": "openapi.tuyain.com",
}


class JarnexTuyaCloud:
    """Backend 3: Tuya-IoT-Cloud-API.

    Transport: httpx.AsyncClient. Im Test wird ein httpx.MockTransport
    via Constructor injiziert (siehe tests/mock_jarnex_cloud.py).
    """

    backend_id = "tuya_cloud"

    def __init__(
        self,
        *,
        device_id: str,
        project_id: str,
        region: str = "eu",
        access_id: str = "",
        access_key: str = "",
        transport: Any = None,  # httpx.MockTransport | httpx.AsyncBaseTransport
        clock: Any = None,  # callable -> ms-timestamp, fuer Test-Determinismus
    ):
        if region not in REGION_HOSTS:
            raise JarnexError(f"region muss einer sein: {list(REGION_HOSTS)}, bekam {region!r}")
        self.device_id = device_id
        self.project_id = project_id
        self.region = region
        self.access_id = access_id
        self.access_key = access_key
        self._base_url = f"https://{REGION_HOSTS[region]}"
        self._transport = transport
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._last_event_ts: int = 0
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            try:
                import httpx  # type: ignore
            except ImportError as e:
                raise JarnexError("httpx nicht installiert") from e
            kwargs: dict[str, Any] = {"base_url": self._base_url, "timeout": 10.0}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    _EMPTY_BODY_SHA256 = (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )

    def _sign(
        self,
        method: str,
        url_path: str,
        ts: int,
        with_token: bool = False,
        body_sha256: str | None = None,
    ) -> str:
        """Tuya-IoT v2 HMAC-SHA256 Signing.

        sign = HMAC-SHA256(access_key, access_id + (access_token if with_token else '') + ts + nonce + stringToSign)
        stringToSign = method.upper() + '\n' + Content-SHA256 + '\n' + headers + '\n' + url_path

        WICHTIG (Bug-Fix 2026-05-29): bei POST mit body MUSS body_sha256 vom
        echten Body kommen, nicht der empty-Hash. Sonst lehnt Tuya mit
        "sign invalid" ab. Default empty-Hash nur fuer GET-Requests.
        """
        sha = body_sha256 or self._EMPTY_BODY_SHA256
        string_to_sign = f"{method.upper()}\n{sha}\n\n{url_path}"
        token = self._access_token if (with_token and self._access_token) else ""
        nonce = ""  # optional
        signing_str = f"{self.access_id}{token}{ts}{nonce}{string_to_sign}"
        return hmac.new(
            self.access_key.encode("utf-8"),
            signing_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()

    async def _request(
        self,
        method: str,
        url_path: str,
        *,
        json_body: dict[str, Any] | None = None,
        with_token: bool = True,
    ) -> dict[str, Any]:
        ts = self._clock()
        # Pre-serialize body damit HMAC-Sign der gleichen Bytes wie HTTP-Send
        # rechnet. Wenn httpx den body anders serialisiert (z.B. spaces nach
        # separators), wuerde body-sha256 mismatchen.
        body_bytes: bytes | None = None
        body_sha = self._EMPTY_BODY_SHA256
        if json_body is not None:
            body_bytes = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            body_sha = hashlib.sha256(body_bytes).hexdigest()
        sign = self._sign(
            method, url_path, ts, with_token=with_token, body_sha256=body_sha,
        )
        headers = {
            "client_id": self.access_id,
            "sign": sign,
            "sign_method": "HMAC-SHA256",
            "t": str(ts),
            "Content-Type": "application/json",
        }
        if with_token and self._access_token:
            headers["access_token"] = self._access_token

        client = self._get_client()
        try:
            # Sende die exakt selben Bytes wie HMAC signed (kein json= damit
            # httpx nicht eigene Whitespace einfuegt)
            if body_bytes is not None:
                resp = await client.request(method, url_path, headers=headers, content=body_bytes)
            else:
                resp = await client.request(method, url_path, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise JarnexUnreachable(f"Tuya-Cloud-Request {url_path} fehlgeschlagen: {e}") from e
        if resp.status_code >= 500:
            raise JarnexUnreachable(f"Tuya-Cloud HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise JarnexError(f"Tuya-Cloud Non-JSON Response: {resp.text[:200]}") from e
        if not isinstance(data, dict) or not data.get("success", True):
            raise JarnexError(f"Tuya-Cloud Fehler: {data.get('msg') or data.get('code') or data}")
        return data

    async def login(self) -> None:
        """Token holen via /v1.0/token?grant_type=1."""
        if not self.access_id or not self.access_key:
            raise JarnexAuthError("access_id / access_key leer")
        data = await self._request("GET", "/v1.0/token?grant_type=1", with_token=False)
        result = data.get("result") or {}
        token = result.get("access_token")
        expire = result.get("expire_time") or 7200
        if not token:
            raise JarnexAuthError("Token fehlt in Tuya-Cloud-Response")
        self._access_token = token
        self._token_expires_at = time.time() + float(expire) - 60.0
        logger.info("Tuya-Cloud-Login OK, Token expires in %ss", expire)

    async def _ensure_token(self) -> None:
        if not self._access_token or time.time() >= self._token_expires_at:
            await self.login()

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
        self._access_token = None

    async def get_state(self) -> dict[str, Any]:
        await self._ensure_token()
        path = f"/v1.0/iot-03/devices/{self.device_id}/status"
        data = await self._request("GET", path)
        dps_list = data.get("result") or []
        dps = {item["code"]: item["value"] for item in dps_list if isinstance(item, dict)}
        return {
            "light_on": bool(dps.get("switch_led") or dps.get("light_switch")),
            "brightness": dps.get("bright_value") or dps.get("brightness"),
            "motion_armed": bool(dps.get("motion_switch")),
            "ai_person_armed": bool(dps.get("ai_person_switch")),
            "raw_codes": dps,
        }

    async def _send_command(self, code: str, value: Any) -> dict[str, Any]:
        await self._ensure_token()
        path = f"/v1.0/iot-03/devices/{self.device_id}/commands"
        body = {"commands": [{"code": code, "value": value}]}
        return await self._request("POST", path, json_body=body)

    async def set_light(self, on: bool, brightness: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"switch": await self._send_command("switch_led", bool(on))}
        if brightness is not None:
            brightness = max(0, min(100, int(brightness)))
            out["brightness"] = await self._send_command("bright_value", brightness)
        return out

    async def ptz(self, op: str, duration_s: float = 1.0) -> dict[str, Any]:
        op_norm = (op or "").lower()
        if op_norm not in ("up", "down", "left", "right", "stop"):
            raise JarnexError(f"PTZ op muss up/down/left/right/stop sein, bekam {op!r}")
        result = await self._send_command("ptz_control", op_norm)
        if op_norm != "stop" and duration_s > 0:
            await asyncio.sleep(min(duration_s, 10.0))
            stop = await self._send_command("ptz_control", "stop")
            return {"move": result, "stop": stop}
        return {"move": result}

    async def trigger_siren(self) -> dict[str, Any]:
        r = await self._send_command("siren_switch", True)
        return {"siren": r}

    async def get_snapshot(self) -> bytes:
        """Tuya-Cloud-Snapshot via /v1.0/iot-03/devices/{id}/picture - liefert
        eine signed-URL. Wir fetchen die URL und returnen die Bytes."""
        await self._ensure_token()
        path = f"/v1.0/iot-03/devices/{self.device_id}/picture"
        data = await self._request("GET", path)
        url = (data.get("result") or {}).get("url")
        if not url:
            raise JarnexError("Tuya-Cloud Snapshot: keine URL in Response")
        client = self._get_client()
        resp = await client.get(url)
        if resp.status_code != 200:
            raise JarnexError(f"Snapshot-Download HTTP {resp.status_code}")
        return resp.content

    async def poll_events(self) -> list[JarnexEvent]:
        """Cloud-Events via /v1.0/iot-03/devices/{id}/logs?type=7 (= DP-Event).

        Wir paginieren NICHT - nur die letzten ~20 Events nach last_event_ts.
        """
        await self._ensure_token()
        now_ms = self._clock()
        start_ms = self._last_event_ts or (now_ms - 60_000)
        path = (
            f"/v1.0/iot-03/devices/{self.device_id}/logs"
            f"?type=7&start_time={start_ms}&end_time={now_ms}&size=20"
        )
        data = await self._request("GET", path)
        logs = (data.get("result") or {}).get("logs") or []
        events: list[JarnexEvent] = []
        for entry in logs:
            code = entry.get("code") or ""
            value = entry.get("value")
            ts = int(entry.get("event_time") or 0)
            label = None
            if code in ("motion_alarm", "motion_event") and value in (True, "true", 1, "1"):
                label = "motion"
            elif code in ("ai_human", "ai_person") and value in (True, "true", 1, "1"):
                label = "ai_person"
            if label:
                events.append(JarnexEvent(
                    label=label,
                    score=None,
                    raw={"code": code, "value": value, "event_time": ts},
                    source_backend=self.backend_id,
                ))
            if ts > self._last_event_ts:
                self._last_event_ts = ts
        return events

    def get_stream_url(self) -> str | None:
        """Tuya-Cloud kann eine RTSP/HLS-URL liefern, das ist aber pro-Call
        signed + token-limitiert. Sync-Methode kann nichts fetchen -
        Caller muss `await get_live_stream_url()` separat triggern."""
        return None

    async def get_live_stream_url(self) -> str | None:
        """Async-Variante: holt eine signed HLS-URL via /v1.0/iot-03/devices/{id}/stream/actions/allocate."""
        await self._ensure_token()
        path = f"/v1.0/iot-03/devices/{self.device_id}/stream/actions/allocate"
        try:
            data = await self._request("POST", path, json_body={"type": "hls"})
        except JarnexError as e:
            logger.info("Stream-Allocation Cloud nicht verfuegbar: %s", e)
            return None
        return (data.get("result") or {}).get("url")
