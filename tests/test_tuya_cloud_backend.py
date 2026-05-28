"""
Tuya-Cloud-Backend-Tests gegen httpx.MockTransport.
"""
from __future__ import annotations

import time

import pytest

from jarnex_tuya_cloud import JarnexTuyaCloud
from tests.mock_jarnex_cloud import build_mock_transport


def _build(handler=None, **kwargs):
    h, transport = build_mock_transport(handler)
    backend = JarnexTuyaCloud(
        device_id="bf-fake",
        project_id="proj-1",
        region="eu",
        access_id="aid",
        access_key="akey",
        transport=transport,
        **kwargs,
    )
    return backend, h


@pytest.mark.asyncio
async def test_login_caches_token():
    backend, handler = _build()
    await backend.login()
    assert backend._access_token == "tok-fake-123"
    assert "GET /v1.0/token" in handler.call_log


@pytest.mark.asyncio
async def test_get_state_returns_high_level_keys():
    backend, handler = _build()
    await backend.login()
    state = await backend.get_state()
    assert state["light_on"] is False
    assert state["brightness"] == 50
    assert state["motion_armed"] is True


@pytest.mark.asyncio
async def test_set_light_sends_switch_command():
    backend, handler = _build()
    await backend.login()
    await backend.set_light(on=True, brightness=70)
    codes = [c.get("code") for c in handler.command_log]
    assert "switch_led" in codes
    assert "bright_value" in codes


@pytest.mark.asyncio
async def test_get_snapshot_two_step():
    backend, handler = _build()
    await backend.login()
    jpeg = await backend.get_snapshot()
    assert jpeg.startswith(b"\xff\xd8")  # JPEG-Magic
    paths = handler.call_log
    assert any("picture" in p for p in paths)


@pytest.mark.asyncio
async def test_poll_events_returns_only_new_after_last_ts():
    from tests.mock_jarnex_cloud import FakeTuyaCloudHandler
    handler = FakeTuyaCloudHandler()
    backend, _ = _build(handler)
    await backend.login()
    now_ms = int(time.time() * 1000)
    handler.add_event("motion_alarm", True, event_time=now_ms - 10000)
    events = await backend.poll_events()
    assert any(e.label == "motion" for e in events)

    # 2. Poll ohne neue Events: leer (last_event_ts hat advanced)
    events2 = await backend.poll_events()
    assert events2 == []

    # 3. Neues Event
    handler.add_event("ai_person", True, event_time=int(time.time() * 1000) + 1000)
    events3 = await backend.poll_events()
    assert any(e.label == "ai_person" for e in events3)


def test_sign_is_deterministic_with_fixed_clock():
    fixed_ts = 1700000000000
    backend = JarnexTuyaCloud(
        device_id="d", project_id="p", region="eu",
        access_id="aid", access_key="akey",
        clock=lambda: fixed_ts,
    )
    s1 = backend._sign("GET", "/v1.0/token?grant_type=1", fixed_ts, with_token=False)
    s2 = backend._sign("GET", "/v1.0/token?grant_type=1", fixed_ts, with_token=False)
    assert s1 == s2
    assert len(s1) == 64  # SHA256 hex


def test_invalid_region_raises():
    with pytest.raises(Exception):
        JarnexTuyaCloud(device_id="d", project_id="p", region="xx", access_id="a", access_key="k")
