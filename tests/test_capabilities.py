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
    # Liveness: Mock liefert DPs -> raw_dps non-empty -> live_probe_ok=True
    assert summary["live_probe_ok"] is True


@pytest.mark.asyncio
async def test_probe_tuya_lan_empty_dps_means_not_live():
    """Regression: tinytuya liefert bei falschem local_key ein leeres dps-Dict
    statt Exception. Liveness-Check muss das fangen, nicht durchwinken."""
    backend = JarnexTuyaLAN(
        device_id="d", host="10.0.0.1", local_key="k",
        transport=FakeTuyaTransport(initial_dps={}),  # leer == falscher key
    )
    summary = await caps.probe(backend)
    assert summary["live_probe_ok"] is False


@pytest.mark.asyncio
async def test_probe_tuya_lan_transport_error_means_not_live():
    transport = FakeTuyaTransport(initial_dps={1: True})
    transport.fail_next(99)  # alle nachfolgenden Calls werfen
    backend = JarnexTuyaLAN(
        device_id="d", host="10.0.0.1", local_key="k", transport=transport,
    )
    summary = await caps.probe(backend)
    assert summary["live_probe_ok"] is False


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
    # Mock liefert state mit raw_codes -> live_probe_ok=True
    assert summary["live_probe_ok"] is True


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
    # RTSP-State liefert immer stream_url -> live_probe_ok=True
    assert summary["live_probe_ok"] is True


def test_is_live_unit():
    """Direkter Unit-Test des Backend-Validators _is_live (Regression-Anker)."""
    from jarnex_capabilities import _is_live
    # tuya_lan
    assert _is_live("tuya_lan", {"raw_dps": {"1": True}}) is True
    assert _is_live("tuya_lan", {"raw_dps": {}}) is False
    assert _is_live("tuya_lan", {}) is False
    assert _is_live("tuya_lan", None) is False
    # tuya_cloud
    assert _is_live("tuya_cloud", {"raw_codes": {"switch_led": True}}) is True
    assert _is_live("tuya_cloud", {"raw_codes": {}}) is False
    # rtsp
    assert _is_live("rtsp", {"stream_url": "rtsp://x/s"}) is True
    assert _is_live("rtsp", {"stream_url": None}) is False
    assert _is_live("rtsp", {}) is False
    # unknown backend
    assert _is_live("nonsense", {"anything": "true"}) is False


def test_serialize_roundtrip():
    summary = {"backend_id": "tuya_lan", "has_light": True, "dp_id_map": {"x": 1}}
    s = caps.serialize(summary)
    out = caps.deserialize(s)
    assert out == summary


def test_deserialize_empty():
    assert caps.deserialize(None) is None
    assert caps.deserialize("") is None
    assert caps.deserialize("not-json") is None
