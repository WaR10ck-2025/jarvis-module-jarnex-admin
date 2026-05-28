"""
playwright_smoke.py - UI-Smoke-Test fuer jarvis-module-jarnex-admin.

Python-Variante des feedback_playwright_msedge_win_fallback Patterns —
gleicher Cascade msedge -> chrome -> bundled chromium.

Pre-Bedingung: uvicorn auf 127.0.0.1:8503 mit 3 Test-Cams (LAN/Cloud/RTSP) im DB.

Aufruf:
    python tests/playwright_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, Page, Error as PWError, Route

BASE = os.environ.get("JARNEX_TEST_BASE", "http://127.0.0.1:8503")
UI_URL = f"{BASE}/modules/jarnex-admin/ui/"
SHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

results: list[dict] = []


def pass_(name: str) -> None:
    results.append({"name": name, "status": "PASS"})
    print(f"[PASS] {name}")


def fail(name: str, detail: str) -> None:
    results.append({"name": name, "status": "FAIL", "detail": detail})
    print(f"[FAIL] {name}: {detail}")


def launch(pw) -> Browser:
    cascade = [
        {"channel": "msedge"},
        {"channel": "chrome"},
        {},
    ]
    for opts in cascade:
        try:
            b = pw.chromium.launch(headless=True, **opts)
            ch = opts.get("channel") or "bundled"
            print(f"Browser geladen (channel={ch})")
            return b
        except PWError as e:
            line = str(e).split("\n")[0]
            print(f"  Channel {opts.get('channel') or 'bundled'} nicht verfuegbar: {line}")
    raise RuntimeError("Kein Browser-Channel verfuegbar")


def main() -> int:
    with sync_playwright() as pw:
        browser = launch(pw)
        console_errors: list[str] = []
        try:
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: console_errors.append(f"PAGEERROR: {err.message}"))

            # Mock /alarms/start + /alarms/status (state-haltig: vor Start = stopped,
            # nach Start = running). refreshAlarmStatus() in der UI ruft /alarms/status
            # auch direkt nach Start.
            alarms_state = {"running": False}

            def mock_alarms_start(route: Route) -> None:
                time.sleep(0.1)
                alarms_state["running"] = True
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "started": True, "task_count": 3, "running": True, "active_tasks": 3,
                    "started_at": int(time.time()), "per_cam": {},
                    "events_total": 0, "errors_total": 0, "cloud_fallbacks": 0, "mqtt_published": 0,
                }))

            def mock_alarms_status(route: Route) -> None:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "running": alarms_state["running"],
                    "active_tasks": 3 if alarms_state["running"] else 0,
                    "started_at": int(time.time()) if alarms_state["running"] else None,
                    "per_cam": {},
                    "events_total": 0, "errors_total": 0, "cloud_fallbacks": 0, "mqtt_published": 0,
                }))

            page.route("**/alarms/start", mock_alarms_start)
            page.route("**/alarms/status", mock_alarms_status)

            # ----- Test 1: UI laedt
            try:
                resp = page.goto(UI_URL, wait_until="networkidle", timeout=8000)
                if resp and resp.ok:
                    pass_("ui-loads-200")
                else:
                    fail("ui-loads-200", f"HTTP {resp.status if resp else 'no-response'}")
            except Exception as e:
                fail("ui-loads-200", str(e))
                return 1

            # ----- Test 2: Header
            header = page.locator("header h1").inner_text()
            if "Jarnex Outdoor Cam Manager" in header:
                pass_("header-title")
            else:
                fail("header-title", header)

            # ----- Test 3: Health-Pill = "ok"
            page.wait_for_function(
                "() => { const t = document.getElementById('health-pill')?.textContent; return t && t.trim() !== '…' && t.trim() !== ''; }",
                timeout=5000,
            )
            health = page.locator("#health-pill").inner_text().strip()
            if health == "ok":
                pass_("health-pill-ok")
            else:
                fail("health-pill-ok", health)

            # ----- Test 4: Version
            version = page.locator("#version-pill").inner_text()
            if "v0.1.0" in version:
                pass_("version-pill-rendered")
            else:
                fail("version-pill-rendered", version)

            # ----- Test 5: Stats-Counter
            stat_count = page.locator("#stat-count").inner_text().strip()
            if stat_count.isdigit():
                pass_("stat-count-rendered")
            else:
                fail("stat-count-rendered", stat_count)

            # ----- Test 6: Backend-Counts
            lan = page.locator("#stat-lan").inner_text().strip()
            cloud = page.locator("#stat-cloud").inner_text().strip()
            rtsp = page.locator("#stat-rtsp").inner_text().strip()
            if lan.isdigit() and cloud.isdigit() and rtsp.isdigit():
                pass_(f"backend-counts-rendered (lan={lan} cloud={cloud} rtsp={rtsp})")
            else:
                fail("backend-counts-rendered", f"lan={lan} cloud={cloud} rtsp={rtsp}")

            # ----- Test 7: Cameras-Table
            page.wait_for_function(
                "() => { const list = document.getElementById('cameras-list'); return list && (list.querySelector('table') || list.textContent.includes('Keine')); }",
                timeout=5000,
            )
            table_count = page.locator("#cameras-list table").count()
            if table_count >= 1:
                pass_("cameras-table-present")
            else:
                fail("cameras-table-present", "no table — seed was needed")

            # ----- Test 8: 3 Rows
            rows = page.locator("#cameras-list table tbody tr").count()
            if rows == 3:
                pass_(f"cameras-three-rows ({rows})")
            else:
                fail("cameras-three-rows", str(rows))

            # ----- Test 9: Badges
            lan_b = page.locator("#cameras-list .backend-badge.tuya_lan").count()
            cloud_b = page.locator("#cameras-list .backend-badge.tuya_cloud").count()
            rtsp_b = page.locator("#cameras-list .backend-badge.rtsp").count()
            if lan_b >= 1 and cloud_b >= 1 and rtsp_b >= 1:
                pass_(f"backend-badges-present (lan={lan_b} cloud={cloud_b} rtsp={rtsp_b})")
            else:
                fail("backend-badges-present", f"lan={lan_b} cloud={cloud_b} rtsp={rtsp_b}")

            # ----- Test 10: RTSP-Indikator
            rtsp_row = page.locator("#cameras-list table tbody tr").filter(has_text="rtsp-1").first
            rtsp_row_text = rtsp_row.inner_text()
            if "RTSP" in rtsp_row_text:
                pass_("rtsp-stream-indicator")
            else:
                fail("rtsp-stream-indicator", rtsp_row_text[:80])

            # ----- Test 11: CIDR-Default
            cidr_val = page.locator("#cidr-input").input_value()
            if cidr_val.startswith("192.168"):
                pass_("discovery-cidr-default")
            else:
                fail("discovery-cidr-default", cidr_val)

            # ----- Test 12: Discovery scan
            page.fill("#cidr-input", "198.51.100.0/30")
            page.fill("#timeout-input", "0.1")
            page.click('section:has-text("Discovery") button:has-text("Scan")')
            page.wait_for_function(
                "() => { const s = document.getElementById('discover-status')?.textContent || ''; return s.includes('Host') || s.includes('Fehler'); }",
                timeout=5000,
            )
            disc_status = page.locator("#discover-status").inner_text()
            if "Host" in disc_status and "Fehler" not in disc_status:
                pass_(f"discovery-scan-runs ({disc_status})")
            else:
                fail("discovery-scan-runs", disc_status)

            # ----- Test 13: Alarm-Start (mocked). Wait_for_function muss auf
            # "Running: true" warten, NICHT nur "Running:" - sonst matched
            # bereits der initiale "Running: false"-State.
            page.click('section:has-text("Alarm-Listener") button:has-text("Start")')
            page.wait_for_function(
                "() => { const t = document.getElementById('alarms-status-text')?.textContent || ''; return t.includes('Running: true'); }",
                timeout=5000,
            )
            alarm_text = page.locator("#alarms-status-text").inner_text()
            if "Running: true" in alarm_text:
                pass_("alarm-start-toggles-status")
            else:
                fail("alarm-start-toggles-status", alarm_text)

            # ----- Screenshot
            shot_path = SHOTS_DIR / "jarnex-ui-final.png"
            page.screenshot(path=str(shot_path), full_page=True)
            pass_(f"screenshot-saved ({shot_path.name})")

            # ----- Test 14: Console-Errors (favicon whitelist)
            real_errors = [e for e in console_errors
                          if "favicon" not in e.lower() and "404" not in e]
            if not real_errors:
                pass_("no-console-errors")
            else:
                fail("no-console-errors", f"{len(real_errors)} errors: " + " | ".join(real_errors[:3]))

            ctx.close()
        finally:
            browser.close()

    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    print(f"\n=== Summary: {passed}/{total} passed ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
