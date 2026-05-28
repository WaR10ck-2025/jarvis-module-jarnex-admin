"""
jarnex_discovery.py - Jarnex-Cam-Discovery im LAN.

Zwei Pfade kombiniert:

  A) TCP-Port-Probe gegen CIDR:
     RTSP (554) + ONVIF (8000) + Tuya (6668). Heuristik:
       - Nur 6668 offen          -> tuya_likely, score=70  (typisch Stock-Jarnex)
       - 6668 + 554 + 8000       -> tuya_with_rtsp, score=100 (Stock-ONVIF aktiv)
       - 554 + 8000 ohne 6668    -> rtsp_likely, score=80 (Post-OpenIPC oder Generic-ONVIF)

  B) Tuya-UDP-Broadcast (Port 6666 + 6667, encrypted heartbeat):
     Tuya-Devices senden alle ~10s ein UDP-Discovery-Packet. tinytuya kann
     das dekodieren und liefert device_id + ip + version. Phase-1: optional,
     Caller kann include_tuya_udp=False setzen.

Phase-1 MVP: A) ist Pflicht, B) ist best-effort (tinytuya muss installiert sein).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any, Optional

logger = logging.getLogger("jarvis.module.jarnex_admin.discovery")

PORT_RTSP = 554
PORT_ONVIF = 8000
PORT_TUYA = 6668

DEFAULT_PORTS: tuple[int, ...] = (PORT_RTSP, PORT_ONVIF, PORT_TUYA)


async def _probe_tcp(host: str, port: int, timeout: float) -> bool:
    """True wenn TCP-Connect innerhalb timeout klappt."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass
    finally:
        pass
    return True


def _classify(open_ports: set[int]) -> tuple[str, int]:
    """Bestimmt likely_backend + score.

    Returns:
        (likely_backend, score) - backend ist 'tuya_lan' / 'rtsp' / 'unknown'
    """
    has_rtsp = PORT_RTSP in open_ports
    has_onvif = PORT_ONVIF in open_ports
    has_tuya = PORT_TUYA in open_ports

    if has_tuya and has_rtsp and has_onvif:
        return "tuya_with_rtsp", 100
    if has_tuya:
        return "tuya_lan", 70
    if has_rtsp and has_onvif:
        return "rtsp", 80
    return "unknown", 0


async def _probe_host(host: str, ports: tuple[int, ...], timeout: float) -> Optional[dict[str, Any]]:
    """Probiert die uebergebenen Ports parallel. Returns dict oder None wenn alle zu."""
    results = await asyncio.gather(
        *(_probe_tcp(host, p, timeout=timeout) for p in ports),
        return_exceptions=True,
    )
    open_ports = {p for p, ok in zip(ports, results) if ok is True}
    if not open_ports:
        return None
    likely, score = _classify(open_ports)
    return {
        "ip": host,
        "open_ports": sorted(open_ports),
        "rtsp_open": PORT_RTSP in open_ports,
        "onvif_open": PORT_ONVIF in open_ports,
        "tuya_open": PORT_TUYA in open_ports,
        "likely_backend": likely,
        "jarnex_likely": likely in ("tuya_lan", "tuya_with_rtsp"),
        "score": score,
    }


async def scan_subnet(
    cidr: str = "192.168.10.0/24",
    *,
    timeout: float = 1.5,
    concurrency: int = 64,
) -> list[dict[str, Any]]:
    """Async TCP-Probe-Discovery ueber das ganze CIDR.

    Returns:
        Liste sortiert nach score DESC, dann IP. Nur Hosts mit min. 1 offenem Port.
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"Ungueltige CIDR: {cidr!r} ({e})") from e

    hosts = [str(h) for h in net.hosts()]
    sem = asyncio.Semaphore(concurrency)

    async def _bound(host: str) -> Optional[dict[str, Any]]:
        async with sem:
            return await _probe_host(host, DEFAULT_PORTS, timeout=timeout)

    raw = await asyncio.gather(*(_bound(h) for h in hosts))
    results = [r for r in raw if r is not None]
    results.sort(key=lambda r: (-r["score"], r["ip"]))
    return results


async def probe_single_host(
    host: str,
    *,
    timeout: float = 1.5,
) -> Optional[dict[str, Any]]:
    """Convenience: einzelnen Host probieren."""
    return await _probe_host(host, DEFAULT_PORTS, timeout=timeout)


async def tuya_udp_listen(duration_s: float = 5.0) -> list[dict[str, Any]]:
    """Tuya-UDP-Broadcast-Listener auf Port 6666 + 6667.

    Tuya-Devices broadcasten encrypted heartbeats. Phase-1: liefert nur IPs
    der absendenden Hosts ohne Decode (tinytuya.scan haette das aber).

    Best-effort: bei Permission-Errors (root noetig fuer raw UDP-Listen) wird
    leere Liste returnt + Log-Warning. Fallback: TCP-Scan macht eh den Job.
    """
    addrs: dict[str, dict[str, Any]] = {}
    loop = asyncio.get_running_loop()

    class _Protocol(asyncio.DatagramProtocol):
        def __init__(self, port: int):
            self.port = port

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            ip = addr[0]
            entry = addrs.setdefault(ip, {"ip": ip, "ports_seen": [], "payload_len": 0})
            if self.port not in entry["ports_seen"]:
                entry["ports_seen"].append(self.port)
            entry["payload_len"] = max(entry["payload_len"], len(data))

    transports: list[asyncio.DatagramTransport] = []
    try:
        for port in (6666, 6667):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda p=port: _Protocol(p),
                    local_addr=("0.0.0.0", port),
                    allow_broadcast=True,
                    reuse_port=False,
                )
                transports.append(transport)
            except (OSError, PermissionError) as e:
                logger.info("Tuya-UDP-Listen Port %s nicht moeglich (%s) - ueberspringe", port, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("Tuya-UDP-Listen unerwarteter Fehler: %s", e)
        return []
    try:
        await asyncio.sleep(duration_s)
    finally:
        for t in transports:
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass
    return list(addrs.values())
