"""
mock_jarnex_cloud.py - httpx.MockTransport-Handler fuer Tuya-IoT-Cloud-API.

Subset, das den Test-Bedarf abdeckt:
  GET  /v1.0/token?grant_type=1           -> Access-Token
  GET  /v1.0/iot-03/devices/{id}/status   -> DP-Liste
  POST /v1.0/iot-03/devices/{id}/commands -> 200 OK
  GET  /v1.0/iot-03/devices/{id}/picture  -> URL fuer JPEG
  GET  /v1.0/iot-03/devices/{id}/logs     -> Events
  GET  <picture-url>                      -> raw JPEG bytes

Stellt ein httpx.MockTransport-Objekt bereit, das in JarnexTuyaCloud
ueber den `transport=` Constructor-Parameter eingespeist wird.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx


class FakeTuyaCloudHandler:
    """State-haltiger Handler. Tests koennen state programmatisch beeinflussen."""

    def __init__(self):
        self.device_state: dict[str, list[dict[str, Any]]] = {
            "default": [
                {"code": "switch_led", "value": False},
                {"code": "bright_value", "value": 50},
                {"code": "motion_switch", "value": True},
            ],
        }
        self.event_log: list[dict[str, Any]] = []
        self.call_log: list[str] = []
        self.command_log: list[dict[str, Any]] = []
        self._jpeg_bytes: bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"

    def add_event(self, code: str, value: Any, event_time: int | None = None) -> None:
        self.event_log.append({
            "code": code,
            "value": value,
            "event_time": event_time or int(time.time() * 1000),
        })

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        method = request.method
        self.call_log.append(f"{method} {path}")

        if path == "/v1.0/token" and method == "GET":
            return httpx.Response(200, json={
                "success": True,
                "result": {"access_token": "tok-fake-123", "expire_time": 7200},
            })

        if path.startswith("/v1.0/iot-03/devices/") and path.endswith("/status") and method == "GET":
            return httpx.Response(200, json={
                "success": True,
                "result": self.device_state["default"],
            })

        if path.startswith("/v1.0/iot-03/devices/") and path.endswith("/commands") and method == "POST":
            try:
                body = json.loads(request.content)
                self.command_log.extend(body.get("commands", []))
            except Exception:  # noqa: BLE001
                pass
            return httpx.Response(200, json={"success": True, "result": True})

        if path.startswith("/v1.0/iot-03/devices/") and path.endswith("/picture") and method == "GET":
            return httpx.Response(200, json={
                "success": True,
                "result": {"url": "https://fake-cdn.tuya.com/snap-fake.jpg"},
            })

        if path == "/snap-fake.jpg" or path.endswith(".jpg"):
            return httpx.Response(200, content=self._jpeg_bytes,
                                  headers={"content-type": "image/jpeg"})

        if path.startswith("/v1.0/iot-03/devices/") and path.endswith("/logs") and method == "GET":
            params = dict(url.params)
            start = int(params.get("start_time", 0))
            relevant = [e for e in self.event_log if int(e["event_time"]) > start]
            return httpx.Response(200, json={
                "success": True,
                "result": {"logs": relevant, "has_more": False},
            })

        if path.endswith("/stream/actions/allocate") and method == "POST":
            return httpx.Response(200, json={
                "success": True,
                "result": {"url": "https://fake-cdn.tuya.com/stream-fake.m3u8"},
            })

        return httpx.Response(404, json={"success": False, "msg": f"unmocked {method} {path}"})


def build_mock_transport(handler: FakeTuyaCloudHandler | None = None) -> tuple[FakeTuyaCloudHandler, httpx.MockTransport]:
    h = handler or FakeTuyaCloudHandler()
    return h, httpx.MockTransport(h.handle)
