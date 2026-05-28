"""
jarnex_database.py - SQLite-Schema und Helper fuer das Jarnex-Admin-Modul.

Tables:
  cameras       - eine Zeile pro Cam (id, name, host, device_id, backend, stream_url, ...)
  credentials   - separat (Settings-Export ohne Cred-Dump). Tuya-LAN-local_key UND RTSP-User/PW
  capabilities  - DP-Map, Stream-URL-Probe-Result, AI-Caps (cam_id, key, value, refreshed_at)
  settings      - KV (auto_provision, polling_interval_s, cloud_fallback_enabled,
                  tuya_cloud_project_id, tuya_cloud_region, tuya_cloud_access_id, ...)
  events        - cam_id, label, score, raw_json, source_backend, ts - Backend-aware

DB-Pfad: ENV JARNEX_DB_PATH oder data.db neben dieser Datei.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.module.jarnex_admin.database")

_DB_PATH = Path(os.getenv("JARNEX_DB_PATH") or (Path(__file__).resolve().parent / "data.db"))


def db_path() -> Path:
    return _DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cameras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    host            TEXT NOT NULL,
    device_id       TEXT,
    port            INTEGER NOT NULL DEFAULT 6668,
    model           TEXT,
    firmware        TEXT,
    backend         TEXT NOT NULL DEFAULT 'tuya_lan',
    stream_url      TEXT,
    pan_tilt_caps   TEXT,
    light_caps      TEXT,
    notes           TEXT,
    added_at        INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    cam_id          INTEGER PRIMARY KEY,
    local_key       TEXT,
    tuya_version    TEXT NOT NULL DEFAULT '3.3',
    rtsp_user       TEXT,
    rtsp_pass       TEXT,
    FOREIGN KEY (cam_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capabilities (
    cam_id          INTEGER NOT NULL,
    key             TEXT NOT NULL,
    value           TEXT,
    refreshed_at    INTEGER NOT NULL,
    PRIMARY KEY (cam_id, key),
    FOREIGN KEY (cam_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cam_id          INTEGER NOT NULL,
    label           TEXT NOT NULL,
    score           REAL,
    raw_json        TEXT,
    source_backend  TEXT NOT NULL DEFAULT 'tuya_lan',
    ts              INTEGER NOT NULL,
    FOREIGN KEY (cam_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_cam_ts ON events(cam_id, ts DESC);
"""


VALID_BACKENDS = ("tuya_lan", "tuya_cloud", "rtsp")


def ensure_schema() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- camera CRUD

def list_cameras() -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM cameras ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_camera(cam_id: int) -> Optional[dict[str, Any]]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_camera(
    *,
    name: str,
    host: str,
    device_id: Optional[str] = None,
    port: int = 6668,
    model: Optional[str] = None,
    firmware: Optional[str] = None,
    backend: str = "tuya_lan",
    stream_url: Optional[str] = None,
    notes: Optional[str] = None,
    local_key: Optional[str] = None,
    tuya_version: str = "3.3",
    rtsp_user: Optional[str] = None,
    rtsp_pass: Optional[str] = None,
) -> int:
    if backend not in VALID_BACKENDS:
        raise ValueError(f"backend muss einer sein: {VALID_BACKENDS}, bekam {backend!r}")
    now = int(time.time())
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO cameras
               (name, host, device_id, port, model, firmware, backend, stream_url, notes, added_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, host, device_id, port, model, firmware, backend, stream_url, notes, now, now),
        )
        cam_id = cur.lastrowid
        conn.execute(
            "INSERT INTO credentials (cam_id, local_key, tuya_version, rtsp_user, rtsp_pass) VALUES (?,?,?,?,?)",
            (cam_id, local_key, tuya_version, rtsp_user, rtsp_pass),
        )
        conn.commit()
        return cam_id
    finally:
        conn.close()


def update_camera(cam_id: int, **fields: Any) -> bool:
    allowed = {"name", "host", "device_id", "port", "model", "firmware",
               "backend", "stream_url", "pan_tilt_caps", "light_caps", "notes"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return False
    if "backend" in fields and fields["backend"] not in VALID_BACKENDS:
        raise ValueError(f"backend muss einer sein: {VALID_BACKENDS}")
    sets = ", ".join(f"{k} = ?" for k in keys)
    values = [fields[k] for k in keys] + [int(time.time()), cam_id]
    conn = connect()
    try:
        cur = conn.execute(f"UPDATE cameras SET {sets}, updated_at = ? WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_camera(cam_id: int) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_credentials(cam_id: int) -> Optional[dict[str, Any]]:
    """Liefert das gesamte Credentials-Dict (local_key, tuya_version, rtsp_user, rtsp_pass)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT local_key, tuya_version, rtsp_user, rtsp_pass FROM credentials WHERE cam_id = ?",
            (cam_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_credentials(
    cam_id: int,
    *,
    local_key: Optional[str] = None,
    tuya_version: Optional[str] = None,
    rtsp_user: Optional[str] = None,
    rtsp_pass: Optional[str] = None,
) -> bool:
    fields: dict[str, Any] = {}
    if local_key is not None:
        fields["local_key"] = local_key
    if tuya_version is not None:
        fields["tuya_version"] = tuya_version
    if rtsp_user is not None:
        fields["rtsp_user"] = rtsp_user
    if rtsp_pass is not None:
        fields["rtsp_pass"] = rtsp_pass
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [cam_id]
    conn = connect()
    try:
        cur = conn.execute(f"UPDATE credentials SET {sets} WHERE cam_id = ?", values)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------- capabilities

def set_capability(cam_id: int, key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO capabilities (cam_id, key, value, refreshed_at) VALUES (?,?,?,?)",
            (cam_id, key, value, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def get_capability(cam_id: int, key: str) -> Optional[str]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT value FROM capabilities WHERE cam_id = ? AND key = ?", (cam_id, key)
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def list_capabilities(cam_id: int) -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT key, value FROM capabilities WHERE cam_id = ?", (cam_id,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------- settings KV

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- events

def insert_event(
    cam_id: int,
    label: str,
    score: Optional[float],
    raw_json: str,
    source_backend: str = "tuya_lan",
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO events (cam_id, label, score, raw_json, source_backend, ts) VALUES (?,?,?,?,?,?)",
            (cam_id, label, score, raw_json, source_backend, int(time.time())),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_events(cam_id: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        if cam_id is None:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE cam_id = ? ORDER BY ts DESC LIMIT ?",
                (cam_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
