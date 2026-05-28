# jarvis-module-jarnex-admin

JARVIS-Admin-Modul fuer **Jarnex Outdoor Porch-Light-Cameras** (Modell JNOL4 / JN-OL4A) — eine Tuya-OEM-Cam mit Pan/Tilt, AI-Human-Detection, dusk-to-dawn-Lampe und 2-Way-Audio. Modul integriert die Cam in das selbst gehostete Jarvis-OS-System: Discovery, Light/PTZ/Siren-Befehle, Alarm-Events, optional Frigate-Provision.

## Dual-Backend-Architektur

Die Hardware spricht von Werk aus Tuya-LAN auf TCP 6668 — kein ONVIF/RTSP standardmaessig. Daher waehlt das Modul zur Laufzeit zwischen drei Backends ueber den `JarnexBackend`-Protocol:

| Backend | Trigger | Funktionsumfang |
|---|---|---|
| `JarnexTuyaLAN` | Default — `local_key` vorhanden | Light/PTZ/Siren via DP-Map, Snapshot via Snapshot-DP, Events via DP-Polling |
| `JarnexTuyaCloud` | Auto-Fallback nach 3 LAN-Errors (5 min Cooldown vor LAN-Re-Try) | Selbe Operationen via Tuya-IoT-Cloud-API; Events via Cloud-Subscription |
| `JarnexRTSP` | `stream_url` gesetzt (Stock-ONVIF oder nach OpenIPC-Firmware-Replace) | ONVIF/RTSP-Standard; Frigate-Provision ohne Einschraenkung |

Backend-Auswahl in [jarnex_backend.py](jarnex_backend.py): `select_backend(cam_row)`. Backend-Status wird in der UI als Badge angezeigt (LAN / Cloud / RTSP).

## Layout

```
jarvis-module-jarnex-admin/
├── module.json
├── router.py                  # FastAPI APIRouter root
├── jarnex_backend.py          # Protocol + select_backend()
├── jarnex_tuya_lan.py         # Backend 1 (default)
├── jarnex_tuya_cloud.py       # Backend 3 (Event-Fallback)
├── jarnex_rtsp.py             # Backend 2 (Post-OpenIPC)
├── jarnex_database.py         # SQLite-Schema mit backend+stream_url+cloud-Settings
├── jarnex_discovery.py        # Tuya-UDP-Broadcast 6666/6667 + TCP-Probe
├── jarnex_capabilities.py     # DP-Map, Stream-URL-Probe, Pan-Tilt-Range
├── jarnex_alarm_listener.py   # async Task pro Cam, Backend-agnostisch
├── jarnex_frigate_bridge.py   # Bedingt aktiv via stream_url-Check
├── data.db                    # ENV JARNEX_DB_PATH override
├── static/index.html          # Single-File UI mit Backend-Status-Badge
├── _test_server.py            # uvicorn lokal port 8403
└── tests/
    ├── conftest.py
    ├── mock_jarnex_tuya.py
    ├── mock_jarnex_cloud.py
    ├── mock_jarnex_rtsp.py
    └── test_*.py
```

## Lokal-Dev

```powershell
cd C:\Daten\Projekte\jarvis-module-jarnex-admin
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt fastapi uvicorn pytest pytest-asyncio
python _test_server.py
# Smoke: curl http://127.0.0.1:8403/modules/jarnex-admin/api/health
```

UI: `http://127.0.0.1:8403/modules/jarnex-admin/ui/`

Tests:
```powershell
pytest tests/ -v
```

## Endpoints (Reolink-isomorph)

Routing-Order Pflicht: spezifische Pfade VOR `/{cam_id}` (FastAPI matched in Definitions-Reihenfolge).

| Endpoint | Funktion |
|---|---|
| `GET /health` | DB-OK, Cam-Count, Backend-Status-Summary |
| `GET /auth-context` | Header-Scheme (`X-API-Key` falls `ADMIN_API_KEY` ENV) |
| `GET /cameras` | List, `include_summary=true` fuer Backend+Light-State+PTZ-Caps |
| `POST /cameras` | Insert mit `local_key`, `device_id`, optional `dp_id_map` |
| `PATCH /cameras/{cam_id}` | Update name/host/notes |
| `DELETE /cameras/{cam_id}` | Cascade-Delete |
| `POST /discover` | Tuya-UDP-Broadcast + TCP-Probe |
| `POST /discover/host` | Single-Host-Probe |
| `GET /cameras/{cam_id}/capabilities?refresh=false` | DP-Map laden, Stream-URL-Probe |
| `POST /cameras/{cam_id}/ptz` | Up/Down/Left/Right/Stop, Speed, Duration |
| `POST /cameras/{cam_id}/light` | on/off/auto, Brightness 0-100 |
| `POST /cameras/{cam_id}/siren` | Play Alarm-Audio |
| `GET /cameras/{cam_id}/snapshot` | JPEG (DP-Bytes oder RTSP-Frame-Grab) |
| `POST /cameras/{cam_id}/provision-to-frigate` | Conditional — 412 wenn kein Stream-URL |
| `GET /cameras/{cam_id}/provision-preview` | Redacted Payloads |
| `POST /alarms/start` / `stop` | Listener-Lifecycle |
| `GET /alarms/status` / `GET /alarms/recent` | Stats + Event-Liste |
| `GET /settings/{key}` / `PUT /settings/{key}` | KV-Store fuer Backend-Konfig |

## OpenIPC-Firmware-Replace (Phase 5b, manuell)

**Nur wenn SoC=Hi3516E V200/V300** (per Hardware-Bench-Inspection festgestellt) und Phase-0-Probe keinen Stream-URL fand. Garantiert nach Replace: RTSP nativ + lokale AI.

**Risiko-Hinweis:** Brick-Gefahr bei UART-Fehler. Garantie weg. **Kein Modul-Endpoint** — bewusst nur als manueller Workflow, weil ein Brick durch einen REST-Call das Modul-Vertrauen ruinieren wuerde.

Schritte (Detail-Anleitung beim Hardware-Bench-Setup):
1. Stock-Firmware via UART (`flashrom` / `kfetch`) backup nach `/srv/jarvis/firmware-backups/jarnex-jnol4-stock-<date>.bin`
2. OpenIPC-Image auf SD-Karte preparen (Hi3516E-Branch)
3. Cam-Boot in U-Boot-Recovery (UART-Boot-Interrupt waehrend Power-On)
4. Image flashen via `flashcp` / `tftp`
5. Nach Replace: `POST /cameras/{cam_id}` mit `stream_url` setzen → Backend wechselt automatisch zu `JarnexRTSP`
6. Frigate-Bridge aktiv: `POST /cameras/{cam_id}/provision-to-frigate`

## ENV-Variablen

| Variable | Default | Funktion |
|---|---|---|
| `JARNEX_DB_PATH` | `data.db` neben router.py | SQLite-DB-Pfad |
| `JARNEX_FRIGATE_MODULE_URL` | `http://localhost:8300/modules/frigate` | Frigate-Modul-API-Basis |
| `JARNEX_TUYA_REGION` | `eu` | Tuya-Cloud-Region (eu/us/cn/in) — fuer Cloud-Backend |
| `JARNEX_MQTT_BROKER` | leer | Optional: MQTT-Republish der Events |
| `ADMIN_API_KEY` | leer | X-API-Key-Schutz |

## Verwandte Module + Memory

- [jarvis-module-reolink-admin/](C:/Daten/Projekte/jarvis-module-reolink-admin/) — Schwester-Cam-Modul, Vorlage fuer Architektur
- [feedback_jarvis_module_dev_conventions.md](C:/Users/WaR10ck/.claude/projects/c--Daten-Projekte/memory/feedback_jarvis_module_dev_conventions.md) — 8 globale Modul-Regeln
- [feedback_jarvis_admin_module_install_quirks.md](C:/Users/WaR10ck/.claude/projects/c--Daten-Projekte/memory/feedback_jarvis_admin_module_install_quirks.md) — Install-Quirks
- [feedback_module_namespace_collision_sysmodules.md](C:/Users/WaR10ck/.claude/projects/c--Daten-Projekte/memory/feedback_module_namespace_collision_sysmodules.md) — `jarnex_`-Prefix Pflicht
