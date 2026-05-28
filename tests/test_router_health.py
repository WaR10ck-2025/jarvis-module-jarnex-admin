"""
Phase-1 Smoke-Tests: Health + Auth-Context + Cameras-CRUD + Discovery + Settings.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from router import router

    app = FastAPI()
    app.include_router(router, prefix="/modules/jarnex-admin/api")
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    r = client.get("/modules/jarnex-admin/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["module"] == "jarnex-admin"
    assert body["db_ok"] is True
    assert "camera_count" in body
    assert body["version"] == "0.1.0"
    assert "backends" in body
    assert body["backends"]["tuya_lan"] == 0
    assert body["backends"]["tuya_cloud"] == 0
    assert body["backends"]["rtsp"] == 0


def test_auth_context(client):
    r = client.get("/modules/jarnex-admin/api/auth-context")
    assert r.status_code == 200
    body = r.json()
    assert body["module"] == "jarnex-admin"
    assert body["auth_scheme"] in ("X-API-Key", "none")


def test_cameras_empty_initially(client):
    r = client.get("/modules/jarnex-admin/api/cameras")
    assert r.status_code == 200
    assert r.json()["cameras"] == []
    assert r.json()["total"] == 0


def test_cameras_create_tuya_lan(client):
    payload = {
        "name": "porch-front",
        "host": "192.168.10.42",
        "device_id": "bf12345678",
        "backend": "tuya_lan",
        "local_key": "abc123secret",
    }
    r = client.post("/modules/jarnex-admin/api/cameras", json=payload)
    assert r.status_code == 200, r.text
    cam = r.json()["created"]
    assert cam["name"] == "porch-front"
    assert cam["backend"] == "tuya_lan"
    assert cam["port"] == 6668


def test_cameras_create_rtsp(client):
    payload = {
        "name": "porch-rtsp",
        "host": "192.168.10.43",
        "backend": "rtsp",
        "stream_url": "rtsp://192.168.10.43:554/stream0",
        "rtsp_user": "admin",
        "rtsp_pass": "p@ss*123",
        "port": 8000,
    }
    r = client.post("/modules/jarnex-admin/api/cameras", json=payload)
    assert r.status_code == 200, r.text
    cam = r.json()["created"]
    assert cam["backend"] == "rtsp"
    assert cam["stream_url"].endswith("/stream0")


def test_cameras_create_invalid_backend(client):
    r = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "bogus",
        "host": "1.2.3.4",
        "backend": "nonsense",
    })
    assert r.status_code == 400


def test_cameras_update_and_delete(client):
    create = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "tmp",
        "host": "10.0.0.1",
        "backend": "tuya_lan",
        "local_key": "x",
    })
    cam_id = create.json()["created"]["id"]

    upd = client.patch(
        f"/modules/jarnex-admin/api/cameras/{cam_id}",
        json={"notes": "test-note", "model": "JNOL4"},
    )
    assert upd.status_code == 200
    assert upd.json()["camera"]["notes"] == "test-note"
    assert upd.json()["camera"]["model"] == "JNOL4"

    delr = client.delete(f"/modules/jarnex-admin/api/cameras/{cam_id}")
    assert delr.status_code == 200
    assert delr.json()["deleted"] is True


def test_cameras_get_404(client):
    r = client.get("/modules/jarnex-admin/api/cameras/99999")
    assert r.status_code == 404


def test_health_backend_count_after_inserts(client):
    """Nach drei Inserts mit verschiedenen Backends muss /health die Count-Summary zeigen."""
    client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "lan-1", "host": "1.1.1.1", "backend": "tuya_lan", "local_key": "x",
    })
    client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "rtsp-1", "host": "2.2.2.2", "backend": "rtsp",
        "stream_url": "rtsp://2.2.2.2/s",
    })
    client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "cloud-1", "host": "3.3.3.3", "backend": "tuya_cloud",
    })
    h = client.get("/modules/jarnex-admin/api/health").json()
    assert h["camera_count"] == 3
    assert h["backends"]["tuya_lan"] == 1
    assert h["backends"]["rtsp"] == 1
    assert h["backends"]["tuya_cloud"] == 1


def test_settings_kv_roundtrip(client):
    put = client.put(
        "/modules/jarnex-admin/api/settings/cloud_fallback_enabled",
        json={"value": "true"},
    )
    assert put.status_code == 200

    get = client.get("/modules/jarnex-admin/api/settings/cloud_fallback_enabled")
    assert get.status_code == 200
    assert get.json()["value"] == "true"


def test_settings_value_must_be_string(client):
    r = client.put(
        "/modules/jarnex-admin/api/settings/foo",
        json={"value": 123},
    )
    assert r.status_code == 400


def test_credentials_patch(client):
    create = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "cam-creds", "host": "10.0.0.5", "backend": "tuya_lan", "local_key": "old",
    })
    cam_id = create.json()["created"]["id"]

    r = client.patch(
        f"/modules/jarnex-admin/api/cameras/{cam_id}/credentials",
        json={"local_key": "new-key", "tuya_version": "3.4"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] is True
