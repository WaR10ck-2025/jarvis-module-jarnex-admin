"""
Capability-Probe-Tests gegen alle 3 Backend-Mocks.
"""
from __future__ import annotations

import pytest

import jarnex_capabilities as caps
from jarnex_tuya_lan import JarnexTuyaLAN
from jarnex_tuya_cloud import JarnexTuyaCloud
from jarnex_rtsp import JarnexRTSP

from tests.mock_jarnex_tuya import FakeTuyaTransport
from tests.mock_jarnex_cloud import build_mock_transport as cloud_transport
from tests.mock_jarnex_rtsp import build_mock_transport as rtsp_transport


@pytest.mark.asyncio
async def test_probe_tuya_lan_has_light_ptz_siren():
    backend = JarnexTuyaLAN(
        device_id="d", host="10.0.0.1", local_key="k",
        transport=FakeTuyaTransport(initial_dps={1: False, 22: 50}),
    )
    summary = await caps.probe(backend)
    assert summary["backend_id"] == "tuya_lan"
    assert summary["has_light"] is True
    assert summary["has_ptz"] is True
    assert summary["has_siren"] is True
    assert summary["has_snapshot_local"] is False
    assert summary["stream_url"] is None
    assert summary["dp_id_map"]["light_switch"] == 1


@pytest.mark.asyncio
async def test_probe_tuya_cloud():
    _, transport = cloud_transport()
    backend = JarnexTuyaCloud(
        device_id="d", project_id="p", region="eu",
        access_id="aid", access_key="akey", transport=transport,
    )
    await backend.login()
    summary = await caps.probe(backend)
    assert summary["backend_id"] == "tuya_cloud"
    assert summary["has_snapshot_local"] is True


@pytest.mark.asyncio
async def test_probe_rtsp():
    _, transport = rtsp_transport()
    backend = JarnexRTSP(host="10.0.0.1", stream_url="rtsp://10.0.0.1/s", transport=transport)
    await backend.login()
    summary = await caps.probe(backend)
    assert summary["backend_id"] == "rtsp"
    assert summary["stream_url"] == "rtsp://10.0.0.1/s"
    assert summary["has_snapshot_local"] is True
    assert summary["has_ptz"] is False  # Phase-1


def test_serialize_roundtrip():
    summary = {"backend_id": "tuya_lan", "has_light": True, "dp_id_map": {"x": 1}}
    s = caps.serialize(summary)
    out = caps.deserialize(s)
    assert out == summary


def test_deserialize_empty():
    assert caps.deserialize(None) is None
    assert caps.deserialize("") is None
    assert caps.deserialize("not-json") is None
