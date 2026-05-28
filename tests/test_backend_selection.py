"""
Backend-Selection-Tests: select_backend() liefert den richtigen Backend-Typ
abhaengig von cam_row.backend + stream_url + credentials.
"""
from __future__ import annotations

import pytest

from jarnex_backend import (
    NoBackendAvailable,
    select_backend,
)


def test_select_tuya_lan_with_local_key():
    cam = {"id": 1, "host": "10.0.0.1", "backend": "tuya_lan", "device_id": "dev1"}
    creds = {"local_key": "secret"}
    backend = select_backend(cam, creds)
    assert backend.backend_id == "tuya_lan"


def test_select_tuya_lan_without_local_key_raises():
    cam = {"id": 1, "host": "10.0.0.1", "backend": "tuya_lan", "device_id": "dev1"}
    with pytest.raises(NoBackendAvailable, match="local_key"):
        select_backend(cam, {})


def test_select_rtsp_when_stream_url_set():
    cam = {
        "id": 1, "host": "10.0.0.1", "backend": "tuya_lan",
        "stream_url": "rtsp://10.0.0.1/stream", "device_id": "dev1",
    }
    backend = select_backend(cam, {"local_key": "x"})
    assert backend.backend_id == "rtsp"


def test_select_rtsp_without_stream_url_raises():
    cam = {"id": 1, "host": "10.0.0.1", "backend": "rtsp", "device_id": "dev1"}
    with pytest.raises(NoBackendAvailable, match="stream_url"):
        select_backend(cam, {})


def test_select_tuya_cloud_needs_settings():
    cam = {"id": 1, "host": "10.0.0.1", "backend": "tuya_cloud", "device_id": "dev1"}
    with pytest.raises(NoBackendAvailable, match="cloud_settings"):
        select_backend(cam, {}, cloud_settings=None)


def test_select_tuya_cloud_with_settings():
    cam = {"id": 1, "host": "10.0.0.1", "backend": "tuya_cloud", "device_id": "dev1"}
    cloud = {
        "tuya_cloud_project_id": "proj",
        "tuya_cloud_region": "eu",
        "tuya_cloud_access_id": "aid",
        "tuya_cloud_access_key": "akey",
    }
    backend = select_backend(cam, {}, cloud_settings=cloud)
    assert backend.backend_id == "tuya_cloud"
