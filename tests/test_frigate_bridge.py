"""
Frigate-Bridge-Tests: build_provision_payload + Provision-Endpoint Conditional-Modes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import jarnex_frigate_bridge as bridge


def test_payload_with_rtsp_stream():
    p = bridge.build_provision_payload(
        name="cam-1", host="10.0.0.1",
        stream_url="rtsp://10.0.0.1:554/stream0",
        rtsp_user="admin", rtsp_pass="p@ss*123",
    )
    assert p["rtsp_main"].startswith("rtsp://admin:")
    assert "p%40ss%2A123" in p["rtsp_main"]  # encoded
    assert p["_meta"]["mode"] == "rtsp"


def test_payload_with_snapshot_only():
    p = bridge.build_provision_payload(
        name="cam-2", host="10.0.0.2",
        stream_url=None, rtsp_user=None, rtsp_pass=None,
        snapshot_url="http://localhost:8403/modules/jarnex-admin/api/cameras/2/snapshot",
    )
    assert p["still_image_url"].endswith("/snapshot")
    assert p["_meta"]["mode"] == "snapshot_polling"


def test_payload_neither_raises():
    with pytest.raises(bridge.FrigateProvisionError):
        bridge.build_provision_payload(
            name="x", host="1.1.1.1",
            stream_url=None, rtsp_user=None, rtsp_pass=None,
            snapshot_url=None,
        )


def test_redact_payload_masks_password_field():
    p = {
        "name": "x",
        "rtsp_main": "rtsp://admin:secret123@10.0.0.1/s",
        "rtsp_password": "secret123",
    }
    r = bridge.redact_payload(p)
    assert r["rtsp_password"] == "***"
    assert ":***@" in r["rtsp_main"]


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from router import router

    app = FastAPI()
    app.include_router(router, prefix="/modules/jarnex-admin/api")
    with TestClient(app) as c:
        yield c


def test_provision_preview_tuya_lan_blocked(client):
    """Cam ohne stream_url + tuya_lan-Backend -> can_provision False, reason erklaert."""
    r = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "lan-only", "host": "10.0.0.1", "backend": "tuya_lan", "local_key": "k",
    })
    cam_id = r.json()["created"]["id"]
    prev = client.get(f"/modules/jarnex-admin/api/cameras/{cam_id}/provision-preview")
    assert prev.status_code == 200
    body = prev.json()
    assert body["can_provision"] is False
    assert "snapshot_url" in body["reason"] or "stream_url" in body["reason"]


def test_provision_preview_rtsp_ok(client):
    r = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "rtsp-1", "host": "10.0.0.2", "backend": "rtsp",
        "stream_url": "rtsp://10.0.0.2/s", "rtsp_user": "u", "rtsp_pass": "p",
    })
    cam_id = r.json()["created"]["id"]
    prev = client.get(f"/modules/jarnex-admin/api/cameras/{cam_id}/provision-preview")
    assert prev.status_code == 200
    body = prev.json()
    assert body["can_provision"] is True
    assert body["preview"]["rtsp_password"] == "***"


def test_provision_to_frigate_412_for_lan_only(client):
    r = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "lan-only-2", "host": "10.0.0.3", "backend": "tuya_lan", "local_key": "k",
    })
    cam_id = r.json()["created"]["id"]
    p = client.post(f"/modules/jarnex-admin/api/cameras/{cam_id}/provision-to-frigate")
    assert p.status_code == 412
