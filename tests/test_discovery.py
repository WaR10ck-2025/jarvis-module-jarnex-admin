"""
Discovery-Tests: TCP-Probe-Heuristik + Endpoint-Smoke.
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


def test_discover_returns_structure(client):
    """Live-Scan auf /30 (TEST-NET-2) - keine echten Hosts, sauberer leerer Result."""
    r = client.post(
        "/modules/jarnex-admin/api/discover",
        json={"cidr": "198.51.100.0/30", "timeout_s": 0.1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cidr"] == "198.51.100.0/30"
    assert "candidates" in body
    assert "total" in body
    assert "jarnex_likely_count" in body
    assert isinstance(body["candidates"], list)


def test_discover_invalid_cidr_returns_400(client):
    r = client.post(
        "/modules/jarnex-admin/api/discover",
        json={"cidr": "not-a-cidr", "timeout_s": 0.1},
    )
    assert r.status_code == 400


def test_discover_host_single_probe(client):
    r = client.post(
        "/modules/jarnex-admin/api/discover/host",
        json={"host": "198.51.100.99", "timeout_s": 0.1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["host"] == "198.51.100.99"
    assert body["result"] is None  # nichts offen


def test_discover_host_missing_body_returns_400(client):
    r = client.post(
        "/modules/jarnex-admin/api/discover/host",
        json={"timeout_s": 0.1},
    )
    assert r.status_code == 400


def test_classify_tuya_only():
    """Reine Unit-Test der Klassifikator-Funktion."""
    from jarnex_discovery import _classify, PORT_TUYA
    likely, score = _classify({PORT_TUYA})
    assert likely == "tuya_lan"
    assert score == 70


def test_classify_tuya_with_rtsp():
    from jarnex_discovery import _classify, PORT_TUYA, PORT_RTSP, PORT_ONVIF
    likely, score = _classify({PORT_TUYA, PORT_RTSP, PORT_ONVIF})
    assert likely == "tuya_with_rtsp"
    assert score == 100


def test_classify_rtsp_only():
    from jarnex_discovery import _classify, PORT_RTSP, PORT_ONVIF
    likely, score = _classify({PORT_RTSP, PORT_ONVIF})
    assert likely == "rtsp"
    assert score == 80


def test_classify_unknown():
    from jarnex_discovery import _classify
    likely, score = _classify({80, 443})
    assert likely == "unknown"
    assert score == 0
