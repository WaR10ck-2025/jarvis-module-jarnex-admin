"""Tests fuer scene-mode Endpoints (atomare Multi-Function-Operationen).

Mocked _hybrid_set_function — keine echte Cam/Cloud-Verbindung noetig.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from router import router

    app = FastAPI()
    app.include_router(router, prefix="/modules/jarnex-admin/api")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def cam_in_db(client):
    """Erstellt eine Test-Cam, returnt cam_id."""
    r = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "test-scene-cam",
        "host": "192.168.10.224",
        "device_id": "bf729712345fake",
        "backend": "tuya_lan",
        "local_key": "fake-key",
        "tuya_version": "3.4",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["created"]["id"]


def test_scene_mode_unknown_mode(client, cam_in_db):
    r = client.post(f"/modules/jarnex-admin/api/cameras/{cam_in_db}/scene-mode", json={"mode": "invalid"})
    assert r.status_code == 400
    assert "manual_on" in r.json()["detail"]


def test_scene_mode_unknown_cam(client):
    r = client.post("/modules/jarnex-admin/api/cameras/9999/scene-mode", json={"mode": "manual_on"})
    assert r.status_code == 404


@pytest.mark.parametrize("mode,expected_actions", [
    ("manual_on", [("floodlight_switch", True), ("basic_private", True), ("motion_tracking", False)]),
    ("manual_on_no_privacy", [("floodlight_switch", True), ("motion_tracking", False)]),
    ("auto_motion", [("floodlight_switch", False), ("basic_private", False), ("motion_tracking", True)]),
    ("alarm", [("floodlight_switch", True), ("basic_private", False), ("motion_tracking", True)]),
])
def test_scene_mode_calls_correct_actions(client, cam_in_db, mode, expected_actions):
    """Jeder Mode triggert die richtige Action-Sequenz mit korrekten Werten."""
    calls = []

    async def fake_hybrid(cam_id, code, value):
        calls.append((cam_id, code, value))
        return {"path": "lan", "result": {"ok": True}}

    with patch("router._hybrid_set_function", new=fake_hybrid):
        r = client.post(f"/modules/jarnex-admin/api/cameras/{cam_in_db}/scene-mode", json={"mode": mode})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == mode
    assert body["all_ok"] is True
    assert len(body["actions"]) == len(expected_actions)
    actual_seq = [(c, v) for cid, c, v in calls]
    assert actual_seq == expected_actions


def test_scene_mode_partial_failure_marks_all_ok_false(client, cam_in_db):
    """Wenn EINE Action failed, ist all_ok=false, aber andere Actions laufen weiter."""
    async def fake_hybrid(cam_id, code, value):
        if code == "basic_private":
            raise RuntimeError("Cloud unreachable")
        return {"path": "lan", "result": {"ok": True}}

    with patch("router._hybrid_set_function", new=fake_hybrid):
        r = client.post(f"/modules/jarnex-admin/api/cameras/{cam_in_db}/scene-mode", json={"mode": "manual_on"})

    assert r.status_code == 200
    body = r.json()
    assert body["all_ok"] is False
    # Light + motion_tracking sollten ok sein, basic_private failed
    by_code = {a["code"]: a for a in body["actions"]}
    assert by_code["floodlight_switch"]["ok"] is True
    assert by_code["basic_private"]["ok"] is False
    assert "unreachable" in by_code["basic_private"]["error"].lower()
    assert by_code["motion_tracking"]["ok"] is True


def test_group_scene_mode_parallel(client, cam_in_db):
    """Group-Endpoint laeuft fuer alle Cams parallel und liefert Result-Liste."""
    # 2. Cam dazu
    r2 = client.post("/modules/jarnex-admin/api/cameras", json={
        "name": "test-scene-cam-2",
        "host": "192.168.10.225",
        "device_id": "bf729712345fake2",
        "backend": "tuya_lan",
        "local_key": "fake-key-2",
        "tuya_version": "3.4",
    })
    cam_id_2 = r2.json()["created"]["id"]

    calls = []

    async def fake_hybrid(cam_id, code, value):
        calls.append((cam_id, code, value))
        return {"path": "cloud", "result": {"ok": True}}

    with patch("router._hybrid_set_function", new=fake_hybrid):
        r = client.post("/modules/jarnex-admin/api/cameras/scene-mode", json={
            "cam_ids": [cam_in_db, cam_id_2],
            "mode": "auto_motion",
        })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["all_ok"] is True
    assert body["mode"] == "auto_motion"
    assert len(body["results"]) == 2
    # Jede Cam hat 3 actions
    cam_id_calls = {cid for cid, code, val in calls}
    assert cam_id_calls == {cam_in_db, cam_id_2}
    assert len([c for c in calls if c[0] == cam_in_db]) == 3
    assert len([c for c in calls if c[0] == cam_id_2]) == 3


def test_group_scene_mode_missing_cam_ids_reported(client, cam_in_db):
    """Fehlende cam_ids landen in missing_ids, valide werden ausgefuehrt."""
    async def fake_hybrid(cam_id, code, value):
        return {"path": "lan", "result": {"ok": True}}

    with patch("router._hybrid_set_function", new=fake_hybrid):
        r = client.post("/modules/jarnex-admin/api/cameras/scene-mode", json={
            "cam_ids": [cam_in_db, 9999],
            "mode": "manual_on",
        })

    assert r.status_code == 200
    body = r.json()
    assert body["missing_ids"] == [9999]
    assert len(body["results"]) == 1
    assert body["results"][0]["cam_id"] == cam_in_db


def test_group_scene_mode_all_missing_returns_404(client):
    r = client.post("/modules/jarnex-admin/api/cameras/scene-mode", json={
        "cam_ids": [9998, 9999],
        "mode": "manual_on",
    })
    assert r.status_code == 404
