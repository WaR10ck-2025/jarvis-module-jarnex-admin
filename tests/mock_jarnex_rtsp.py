"""
mock_jarnex_rtsp.py - httpx.MockTransport fuer ONVIF/RTSP-Snapshot-Probes.

Phase-1: minimaler Mock. Implementiert nur den Snapshot-HTTP-GET-Pfad.
ONVIF SOAP-Subscription wird Phase 6 mit echtem onvif-zeep-async ergaenzt.
"""
from __future__ import annotations

import httpx


class FakeRTSPHandler:
    def __init__(self, snapshot_path: str = "/onvif/snapshot"):
        self.snapshot_path = snapshot_path
        self._jpeg = b"\xff\xd8\xff\xe0rtsp-fake-snapshot\xff\xd9"
        self.call_log: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.call_log.append(f"{request.method} {request.url.path}")
        if request.url.path == self.snapshot_path:
            return httpx.Response(200, content=self._jpeg, headers={"content-type": "image/jpeg"})
        # Generic 404 — Backend probiert mehrere Pfade
        return httpx.Response(404, content=b"")


def build_mock_transport(handler: FakeRTSPHandler | None = None) -> tuple[FakeRTSPHandler, httpx.MockTransport]:
    h = handler or FakeRTSPHandler()
    return h, httpx.MockTransport(h.handle)
