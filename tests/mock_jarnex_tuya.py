"""
mock_jarnex_tuya.py - Fake-Transport fuer JarnexTuyaLAN.

Implementiert das `TuyaTransport`-Protocol aus jarnex_tuya_lan.py. Kann
DP-Werte programmatisch setzen damit Tests Edge-Trigger-Events erzeugen koennen.
"""
from __future__ import annotations

from typing import Any


class FakeTuyaTransport:
    """In-Memory Tuya-Device-Simulation."""

    def __init__(self, initial_dps: dict[int, Any] | None = None):
        # Tuya speichert DP-IDs als String-Keys ("1", "22", "101", ...)
        self._dps: dict[str, Any] = {}
        if initial_dps:
            for k, v in initial_dps.items():
                self._dps[str(k)] = v
        self.call_log: list[tuple[str, Any]] = []
        self._fail_next: int = 0  # >0 = naechste N Calls schmeissen JarnexUnreachable

    def fail_next(self, n: int) -> None:
        self._fail_next = n

    def set_dp(self, dp_id: int, value: Any) -> None:
        """Test-Helper: DP-Wert direkt setzen (simuliert Motion-Event etc)."""
        self._dps[str(dp_id)] = value

    async def status(self) -> dict[str, Any]:
        if self._fail_next > 0:
            self._fail_next -= 1
            from jarnex_backend import JarnexUnreachable  # type: ignore
            raise JarnexUnreachable("FakeTransport: forced failure")
        self.call_log.append(("status", None))
        return {"devId": "fake-dev", "dps": dict(self._dps)}

    async def set_value(self, dp_id: int, value: Any) -> dict[str, Any]:
        if self._fail_next > 0:
            self._fail_next -= 1
            from jarnex_backend import JarnexUnreachable  # type: ignore
            raise JarnexUnreachable("FakeTransport: forced failure")
        self._dps[str(dp_id)] = value
        self.call_log.append(("set_value", {"dp": dp_id, "value": value}))
        return {"dps": dict(self._dps), "result": "ok"}
