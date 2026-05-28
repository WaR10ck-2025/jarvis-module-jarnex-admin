"""
RTSP-Backend-Tests gegen httpx.MockTransport.
"""
from __future__ import annotations

import pytest

from jarnex_backend import JarnexAuthError, JarnexError
from jarnex_rtsp import JarnexRTSP
from tests.mock_jarnex_rtsp import build_mock_transport


@pytest.mark.asyncio
async def test_login_requires_stream_url():
    backend = JarnexRTSP(host="10.0.0.1", stream_url="")
    with pytest.raises(JarnexAuthError):
        await backend.login()


@pytest.mark.asyncio
async def test_login_ok_with_stream_url():
    backend = JarnexRTSP(host="10.0.0.1", stream_url="rtsp://10.0.0.1/s")
    await backend.login()


@pytest.mark.asyncio
async def test_snapshot_returns_jpeg_via_mock():
    handler, transport = build_mock_transport()
    backend = JarnexRTSP(
        host="10.0.0.1",
        stream_url="rtsp://10.0.0.1/s",
        rtsp_user="admin",
        rtsp_pass="p",
        transport=transport,
    )
    await backend.login()
    jpeg = await backend.get_snapshot()
    assert jpeg.startswith(b"\xff\xd8")


@pytest.mark.asyncio
async def test_set_light_unsupported():
    backend = JarnexRTSP(host="10.0.0.1", stream_url="rtsp://10.0.0.1/s")
    with pytest.raises(JarnexError):
        await backend.set_light(on=True)


@pytest.mark.asyncio
async def test_ptz_not_implemented():
    backend = JarnexRTSP(host="10.0.0.1", stream_url="rtsp://10.0.0.1/s")
    with pytest.raises(NotImplementedError):
        await backend.ptz("left")


@pytest.mark.asyncio
async def test_poll_events_returns_empty_phase1():
    backend = JarnexRTSP(host="10.0.0.1", stream_url="rtsp://10.0.0.1/s")
    events = await backend.poll_events()
    assert events == []


def test_get_stream_url_injects_credentials():
    backend = JarnexRTSP(
        host="10.0.0.1",
        stream_url="rtsp://10.0.0.1:554/stream",
        rtsp_user="admin",
        rtsp_pass="p@ss*123",
    )
    url = backend.get_stream_url()
    # Sonderzeichen MUSS URL-encoded sein
    assert "p%40ss%2A123" in url
    assert "admin" in url


def test_get_stream_url_idempotent_if_credentials_already_in_url():
    backend = JarnexRTSP(
        host="10.0.0.1",
        stream_url="rtsp://admin:secret@10.0.0.1/s",
        rtsp_user="admin",
        rtsp_pass="p",
    )
    url = backend.get_stream_url()
    # @ schon in URL -> nicht nochmal credentials injizieren
    assert url == "rtsp://admin:secret@10.0.0.1/s"
