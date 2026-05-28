"""
Alarm-Listener-Tests: Start/Stop, Edge-Trigger, Cloud-Fallback nach LAN-Errors.
"""
from __future__ import annotations

import asyncio
import json
import pytest

import jarnex_database as db
from jarnex_alarm_listener import AlarmListener


@pytest.fixture()
def listener():
    return AlarmListener()


@pytest.mark.asyncio
async def test_start_stop_idempotent_empty_db(listener):
    started = await listener.start()
    assert started["started"] is True
    assert started["task_count"] == 0

    started_again = await listener.start()
    assert started_again["started"] is False
    assert started_again["reason"] == "already_running"

    stopped = await listener.stop()
    assert stopped["stopped"] is True

    stopped_again = await listener.stop()
    assert stopped_again["stopped"] is False
    assert stopped_again["reason"] == "not_running"


def test_resolve_cloud_settings_requires_access_keys(listener):
    db.set_setting("tuya_cloud_project_id", "proj")
    assert listener._resolve_cloud_settings() is None

    db.set_setting("tuya_cloud_access_id", "aid")
    db.set_setting("tuya_cloud_access_key", "akey")
    out = listener._resolve_cloud_settings()
    assert out is not None
    assert out["tuya_cloud_access_id"] == "aid"


def test_cloud_fallback_enabled_flag(listener):
    db.set_setting("cloud_fallback_enabled", "true")
    assert listener._cloud_fallback_enabled() is True
    db.set_setting("cloud_fallback_enabled", "no")
    assert listener._cloud_fallback_enabled() is False


def test_event_inserted_with_source_backend():
    """Direkt-DB-Test: insert_event mit source_backend speichert + listet zurueck."""
    cam_id = db.insert_camera(
        name="cam-e", host="10.0.0.50", backend="tuya_lan", local_key="x",
    )
    db.insert_event(cam_id, "motion", None, json.dumps({}), source_backend="tuya_cloud")
    events = db.list_events(cam_id=cam_id)
    assert len(events) == 1
    assert events[0]["source_backend"] == "tuya_cloud"
    assert events[0]["label"] == "motion"
