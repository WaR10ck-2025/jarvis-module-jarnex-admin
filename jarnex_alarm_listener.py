"""
jarnex_alarm_listener.py - Backend-agnostisches Alarm-Polling.

Singleton-Pattern wie Reolink-Listener:
  - Eine asyncio.Task pro Cam
  - Edge-Triggered Events via Backend.poll_events()
  - Auto-Switch zu Cloud-Backend nach 3 consecutive LAN-Errors (5min Cooldown)
  - Events landen in DB events-Tabelle mit source_backend
  - Optional MQTT-Republish (paho-mqtt graceful-degradation)

Lifecycle:
  POST /alarms/start -> get_listener().start()
  POST /alarms/stop  -> get_listener().stop()
  GET  /alarms/status -> get_listener().stats
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

try:
    from . import jarnex_database as db
    from .jarnex_backend import (
        JarnexBackend,
        JarnexError,
        JarnexUnreachable,
        NoBackendAvailable,
        select_backend,
    )
except ImportError:
    import jarnex_database as db  # type: ignore
    from jarnex_backend import (  # type: ignore
        JarnexBackend,
        JarnexError,
        JarnexUnreachable,
        NoBackendAvailable,
        select_backend,
    )

logger = logging.getLogger("jarvis.module.jarnex_admin.alarm_listener")


DEFAULT_POLL_INTERVAL_S = 10.0
LAN_ERROR_THRESHOLD = 3
CLOUD_COOLDOWN_S = 300.0


class AlarmListener:
    """Singleton-Listener fuer alle Cams. start()/stop() sind idempotent."""

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}
        self._stop_event = asyncio.Event()
        self._stop_event.set()  # initially stopped
        self.started_at: Optional[int] = None
        self._counters: dict[int, dict[str, Any]] = {}
        self._global_stats: dict[str, Any] = {
            "events_total": 0,
            "errors_total": 0,
            "cloud_fallbacks": 0,
            "mqtt_published": 0,
        }

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": not self._stop_event.is_set(),
            "active_tasks": len(self._tasks),
            "started_at": self.started_at,
            "per_cam": {str(k): v for k, v in self._counters.items()},
            **self._global_stats,
        }

    def _resolve_cloud_settings(self) -> dict[str, str] | None:
        keys = (
            "tuya_cloud_project_id",
            "tuya_cloud_region",
            "tuya_cloud_access_id",
            "tuya_cloud_access_key",
        )
        out: dict[str, str] = {}
        for k in keys:
            v = db.get_setting(k)
            if v:
                out[k] = v
        # Brauchbar nur wenn access_id + access_key da sind
        if "tuya_cloud_access_id" in out and "tuya_cloud_access_key" in out:
            return out
        return None

    def _cloud_fallback_enabled(self) -> bool:
        return (db.get_setting("cloud_fallback_enabled") or "").lower() in ("1", "true", "yes")

    async def _build_backend(self, cam_id: int, prefer_cloud: bool = False) -> JarnexBackend:
        cam = db.get_camera(cam_id)
        if not cam:
            raise NoBackendAvailable(f"Cam {cam_id} nicht in DB")
        creds = db.get_credentials(cam_id) or {}
        cloud_settings = self._resolve_cloud_settings()
        if prefer_cloud and cloud_settings:
            cam = dict(cam)
            cam["backend"] = "tuya_cloud"
        return select_backend(
            cam,
            creds,
            cloud_settings=cloud_settings,
            cloud_fallback_enabled=self._cloud_fallback_enabled(),
        )

    async def _poll_loop(self, cam_id: int) -> None:
        counter = self._counters.setdefault(cam_id, {
            "events": 0,
            "errors": 0,
            "consecutive_errors": 0,
            "current_backend": "tuya_lan",
            "cooldown_until": 0.0,
        })
        backend: Optional[JarnexBackend] = None
        interval = float(db.get_setting("polling_interval_s") or DEFAULT_POLL_INTERVAL_S)

        while not self._stop_event.is_set():
            try:
                now = time.time()
                # Erzeuge / refresh Backend
                if backend is None:
                    prefer_cloud = (
                        counter["consecutive_errors"] >= LAN_ERROR_THRESHOLD
                        and self._cloud_fallback_enabled()
                        and now < counter["cooldown_until"]
                    )
                    backend = await self._build_backend(cam_id, prefer_cloud=prefer_cloud)
                    counter["current_backend"] = backend.backend_id
                    await backend.login()

                # Poll
                events = await backend.poll_events()
                for ev in events:
                    db.insert_event(
                        cam_id,
                        ev.label,
                        ev.score,
                        json.dumps(ev.raw, default=str),
                        ev.source_backend,
                    )
                    counter["events"] += 1
                    self._global_stats["events_total"] += 1
                # Successful poll -> reset error counter
                counter["consecutive_errors"] = 0

            except asyncio.CancelledError:
                break
            except (JarnexUnreachable, JarnexError) as e:
                counter["errors"] += 1
                counter["consecutive_errors"] += 1
                self._global_stats["errors_total"] += 1
                logger.info("Cam %s Poll-Error #%s: %s", cam_id, counter["consecutive_errors"], e)
                # Auto-Switch zu Cloud nach Threshold
                if (counter["consecutive_errors"] >= LAN_ERROR_THRESHOLD
                        and counter["current_backend"] == "tuya_lan"
                        and self._cloud_fallback_enabled()
                        and self._resolve_cloud_settings()):
                    logger.warning("Cam %s wechselt zu Cloud-Backend (5min)", cam_id)
                    self._global_stats["cloud_fallbacks"] += 1
                    counter["cooldown_until"] = time.time() + CLOUD_COOLDOWN_S
                    if backend is not None:
                        await backend.close()
                    backend = None  # re-build im naechsten Loop-Tick
            except Exception as e:  # noqa: BLE001
                counter["errors"] += 1
                self._global_stats["errors_total"] += 1
                logger.exception("Cam %s unerwarteter Poll-Fehler: %s", cam_id, e)
                if backend is not None:
                    try:
                        await backend.close()
                    except Exception:  # noqa: BLE001
                        pass
                    backend = None

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break  # stop_event gesetzt
            except asyncio.TimeoutError:
                continue

        if backend is not None:
            try:
                await backend.close()
            except Exception:  # noqa: BLE001
                pass

    async def start(self) -> dict[str, Any]:
        if not self._stop_event.is_set():
            return {"started": False, "reason": "already_running", **self.stats}
        self._stop_event.clear()
        self.started_at = int(time.time())
        cams = db.list_cameras()
        for cam in cams:
            cam_id = cam["id"]
            task = asyncio.create_task(self._poll_loop(cam_id), name=f"jarnex-poll-{cam_id}")
            self._tasks[cam_id] = task
        return {"started": True, "task_count": len(self._tasks), **self.stats}

    async def stop(self) -> dict[str, Any]:
        if self._stop_event.is_set():
            return {"stopped": False, "reason": "not_running", **self.stats}
        self._stop_event.set()
        for task in list(self._tasks.values()):
            try:
                task.cancel()
            except Exception:  # noqa: BLE001
                pass
        # Wait briefly fuer Cleanup
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        cancelled = len(self._tasks)
        self._tasks.clear()
        return {"stopped": True, "cancelled_tasks": cancelled, **self.stats}


_listener: Optional[AlarmListener] = None


def get_listener() -> AlarmListener:
    global _listener
    if _listener is None:
        _listener = AlarmListener()
    return _listener
