"""
Tuya-LAN-Backend-Tests gegen FakeTuyaTransport.

Pflicht-Pattern: jeder Backend-Call laeuft gegen Mock, kein echtes Netzwerk.
"""
from __future__ import annotations

import pytest

from jarnex_backend import JarnexAuthError, JarnexUnreachable, JarnexEvent
from jarnex_tuya_lan import DEFAULT_DP_MAP, JarnexTuyaLAN

from tests.mock_jarnex_tuya import FakeTuyaTransport


def _build(transport=None, local_key="secret"):
    return JarnexTuyaLAN(
        device_id="bf-fake",
        host="10.0.0.10",
        local_key=local_key,
        transport=transport or FakeTuyaTransport(),
    )


@pytest.mark.asyncio
async def test_login_calls_status_probe():
    transport = FakeTuyaTransport(initial_dps={1: False, 22: 75})
    backend = _build(transport)
    await backend.login()
    assert ("status", None) in transport.call_log


@pytest.mark.asyncio
async def test_login_without_local_key_raises():
    backend = _build(local_key="")
    with pytest.raises(JarnexAuthError):
        await backend.login()


@pytest.mark.asyncio
async def test_get_state_maps_dps_to_high_level_keys():
    # DP-Map nach Smart-Cam-Convention: light=138, brightness=123, motion=107
    transport = FakeTuyaTransport(initial_dps={138: True, 123: 60, 107: True})
    backend = _build(transport)
    state = await backend.get_state()
    assert state["light_on"] is True
    assert state["brightness"] == 60
    assert state["motion_armed"] is True


@pytest.mark.asyncio
async def test_set_light_on_with_brightness():
    transport = FakeTuyaTransport()
    backend = _build(transport)
    await backend.set_light(on=True, brightness=80)
    assert ("set_value", {"dp": 138, "value": True}) in transport.call_log
    assert ("set_value", {"dp": 123, "value": 80}) in transport.call_log


@pytest.mark.asyncio
async def test_set_light_brightness_clamped():
    transport = FakeTuyaTransport()
    backend = _build(transport)
    await backend.set_light(on=True, brightness=999)
    # Letzter set_value-Call muss brightness=100 sein (clamped), DP 123 = ipc_bright
    calls = [c for c in transport.call_log if c[0] == "set_value" and c[1]["dp"] == 123]
    assert calls[-1][1]["value"] == 100


@pytest.mark.asyncio
async def test_ptz_invalid_op_raises():
    backend = _build()
    with pytest.raises(Exception):  # JarnexError
        await backend.ptz("rotate-360")


@pytest.mark.asyncio
async def test_ptz_stop_no_duration_sleep():
    transport = FakeTuyaTransport()
    backend = _build(transport)
    result = await backend.ptz("stop", duration_s=0.0)
    # Stop sendet NUR ptz_stop=True (DP 151). ptz_direction=0 wird NICHT
    # gesendet, weil Jarnex-Cams "0" rejecten und auf "8"=bottom_right
    # fallen lassen (Cam dreht weiter statt zu stoppen).
    assert "stop" in result
    stop_calls = [c for c in transport.call_log if c[0] == "set_value" and c[1]["dp"] == 151]
    assert stop_calls and stop_calls[-1][1]["value"] is True
    # Sanity-Check: ptz_direction (DP 119) wurde NICHT gesetzt
    direction_calls = [c for c in transport.call_log if c[0] == "set_value" and c[1]["dp"] == 119]
    assert not direction_calls, f"ptz_direction sollte nicht gesetzt werden, aber: {direction_calls}"


@pytest.mark.asyncio
async def test_ptz_left_with_duration_sends_stop():
    transport = FakeTuyaTransport()
    backend = _build(transport)
    result = await backend.ptz("left", duration_s=0.01)  # kurz
    assert "move" in result
    assert "stop" in result
    # ptz_control DP=119, Jarnex-Cam-Enum: "3" = left (PTZ_ENUM_MAP["left"])
    ptz_calls = [c for c in transport.call_log if c[0] == "set_value" and c[1]["dp"] == 119]
    assert ptz_calls[0][1]["value"] == "3"
    # Stop kommt ueber ptz_stop (DP 151, Boolean True) ODER ptz_control "0"
    stop_calls = [
        c for c in transport.call_log
        if c[0] == "set_value" and (
            (c[1]["dp"] == 151 and c[1]["value"] is True)
            or (c[1]["dp"] == 119 and c[1]["value"] == "0")
        )
    ]
    assert stop_calls, "ein Stop-Call muss kommen (ptz_stop=True oder ptz_control=0)"


@pytest.mark.asyncio
async def test_trigger_siren():
    transport = FakeTuyaTransport()
    backend = _build(transport)
    await backend.trigger_siren()
    # siren_switch ist DP 134
    siren_calls = [c for c in transport.call_log if c[0] == "set_value" and c[1]["dp"] == 134]
    assert siren_calls[0][1]["value"] is True


@pytest.mark.asyncio
async def test_get_snapshot_lan_not_supported():
    backend = _build()
    with pytest.raises(Exception):  # JarnexError - kein lokaler Snapshot
        await backend.get_snapshot()


@pytest.mark.asyncio
async def test_poll_events_edge_triggered_motion():
    # DP-IDs aus DEFAULT_DP_MAP holen statt hardcoded — Test ist robust gegen Map-Changes
    motion_dp = DEFAULT_DP_MAP["motion_event"]
    ai_dp = DEFAULT_DP_MAP["ai_person_event"]
    transport = FakeTuyaTransport(initial_dps={motion_dp: False, ai_dp: False})
    backend = _build(transport)
    # 1. Poll: keine Events (state = idle)
    events = await backend.poll_events()
    assert events == []

    # 2. Trigger motion event
    transport.set_dp(motion_dp, True)
    events = await backend.poll_events()
    assert len(events) == 1
    assert events[0].label == "motion"
    assert events[0].source_backend == "tuya_lan"

    # 3. Poll erneut: kein dup-Event (Edge-Trigger), state stuck-high
    events = await backend.poll_events()
    assert events == []

    # 4. State faellt zurueck
    transport.set_dp(motion_dp, False)
    await backend.poll_events()
    # 5. State steigt erneut -> erneut Event
    transport.set_dp(motion_dp, True)
    events = await backend.poll_events()
    assert len(events) == 1
    assert events[0].label == "motion"


@pytest.mark.asyncio
async def test_poll_events_ai_person_separate_from_motion():
    motion_dp = DEFAULT_DP_MAP["motion_event"]
    ai_dp = DEFAULT_DP_MAP["ai_person_event"]
    transport = FakeTuyaTransport(initial_dps={motion_dp: False, ai_dp: False})
    backend = _build(transport)
    await backend.poll_events()  # initial

    transport.set_dp(motion_dp, True)
    transport.set_dp(ai_dp, True)
    events = await backend.poll_events()
    labels = {e.label for e in events}
    assert labels == {"motion", "ai_person"}


@pytest.mark.asyncio
async def test_transport_failure_propagates_as_unreachable():
    transport = FakeTuyaTransport()
    transport.fail_next(1)
    backend = _build(transport)
    with pytest.raises(JarnexUnreachable):
        await backend.get_state()


def test_get_stream_url_always_none_for_lan():
    backend = _build()
    assert backend.get_stream_url() is None


def test_default_dp_map_has_expected_keys():
    assert "light_switch" in DEFAULT_DP_MAP
    assert "brightness" in DEFAULT_DP_MAP
    assert "ptz_direction" in DEFAULT_DP_MAP
    assert "motion_event" in DEFAULT_DP_MAP
    assert "ai_person_event" in DEFAULT_DP_MAP
