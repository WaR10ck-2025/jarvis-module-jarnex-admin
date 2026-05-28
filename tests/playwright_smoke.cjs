/**
 * playwright_smoke.cjs - UI-Smoke-Test fuer jarvis-module-jarnex-admin.
 *
 * Pre-Bedingung: uvicorn laeuft auf 127.0.0.1:8503 mit 3 Test-Cams (LAN/Cloud/RTSP) im DB.
 *
 * Aufruf:
 *   NODE_PATH="C:/Users/WaR10ck/AppData/Roaming/npm/node_modules/@playwright/mcp/node_modules" \
 *     node tests/playwright_smoke.cjs
 *
 * Cascade-Fallback msedge -> chrome -> bundled chromium (siehe feedback_playwright_msedge_win_fallback.md).
 */

const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const BASE = process.env.JARNEX_TEST_BASE || 'http://127.0.0.1:8503';
const UI_URL = `${BASE}/modules/jarnex-admin/ui/`;
const SHOTS_DIR = path.resolve(__dirname, 'screenshots');
if (!fs.existsSync(SHOTS_DIR)) fs.mkdirSync(SHOTS_DIR, { recursive: true });

const results = [];
function pass(name) { results.push({ name, status: 'PASS' }); console.log(`[PASS] ${name}`); }
function fail(name, detail) { results.push({ name, status: 'FAIL', detail }); console.error(`[FAIL] ${name}: ${detail}`); }

async function launchBrowser() {
  const cascade = [
    { headless: true, channel: 'msedge' },
    { headless: true, channel: 'chrome' },
    { headless: true },
  ];
  for (const opts of cascade) {
    try {
      const b = await chromium.launch(opts);
      console.log(`Browser geladen (channel=${opts.channel || 'bundled'})`);
      return b;
    } catch (e) {
      console.log(`  Channel ${opts.channel || 'bundled'} nicht verfuegbar: ${e.message.split('\n')[0]}`);
    }
  }
  throw new Error('Kein Browser-Channel verfuegbar');
}

(async () => {
  const browser = await launchBrowser();
  const consoleErrors = [];
  try {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(`PAGEERROR: ${err.message}`));

    // ---------- Test 1: UI laedt ohne Crash
    try {
      const resp = await page.goto(UI_URL, { waitUntil: 'networkidle', timeout: 8000 });
      if (resp && resp.ok()) pass('ui-loads-200');
      else fail('ui-loads-200', `HTTP ${resp ? resp.status() : 'no-response'}`);
    } catch (e) {
      fail('ui-loads-200', e.message);
      throw e;
    }

    // ---------- Test 2: Header zeigt "Jarnex Outdoor Cam Manager"
    const headerText = await page.locator('header h1').innerText();
    if (headerText.includes('Jarnex Outdoor Cam Manager')) pass('header-title');
    else fail('header-title', `Got: ${headerText}`);

    // ---------- Test 3: Health-Pill = "ok" (rendered, nicht initial "…")
    await page.waitForFunction(() => {
      const t = document.getElementById('health-pill')?.textContent;
      return t && t !== 'lade' && t !== '…';
    }, { timeout: 5000 });
    const healthText = await page.locator('#health-pill').innerText();
    if (healthText.trim() === 'ok') pass('health-pill-ok');
    else fail('health-pill-ok', `Got: ${healthText}`);

    // ---------- Test 4: Version "v0.1.0"
    const versionText = await page.locator('#version-pill').innerText();
    if (versionText.includes('v0.1.0')) pass('version-pill-rendered');
    else fail('version-pill-rendered', `Got: ${versionText}`);

    // ---------- Test 5: Stats-Counter sind keine "…" mehr (rendered)
    const statCount = await page.locator('#stat-count').innerText();
    if (statCount.match(/^\d+$/)) pass('stat-count-rendered');
    else fail('stat-count-rendered', `Got: ${statCount}`);

    // ---------- Test 6: Backend-Counts pro Backend
    const lanCount = await page.locator('#stat-lan').innerText();
    const cloudCount = await page.locator('#stat-cloud').innerText();
    const rtspCount = await page.locator('#stat-rtsp').innerText();
    if (lanCount.match(/^\d+$/) && cloudCount.match(/^\d+$/) && rtspCount.match(/^\d+$/)) pass('backend-counts-rendered');
    else fail('backend-counts-rendered', `lan=${lanCount} cloud=${cloudCount} rtsp=${rtspCount}`);

    // ---------- Test 7: Cameras-List rendert eine Tabelle, nicht leeren State
    await page.waitForFunction(() => {
      const list = document.getElementById('cameras-list');
      return list && (list.querySelector('table') || list.textContent.includes('Keine Cameras'));
    }, { timeout: 5000 });
    const hasTable = await page.locator('#cameras-list table').count();
    if (hasTable >= 1) pass('cameras-table-present');
    else fail('cameras-table-present', 'no table — Cameras-Seed war noetig vor dem Smoke');

    // ---------- Test 8: 3 Cam-Rows (LAN, Cloud, RTSP)
    const rowCount = await page.locator('#cameras-list table tbody tr').count();
    if (rowCount === 3) pass('cameras-three-rows');
    else fail('cameras-three-rows', `Got ${rowCount}`);

    // ---------- Test 9: Backend-Badges rendern mit korrekter Farb-Klasse
    const lanBadge = await page.locator('#cameras-list .backend-badge.tuya_lan').count();
    const cloudBadge = await page.locator('#cameras-list .backend-badge.tuya_cloud').count();
    const rtspBadge = await page.locator('#cameras-list .backend-badge.rtsp').count();
    if (lanBadge >= 1 && cloudBadge >= 1 && rtspBadge >= 1) pass('backend-badges-present');
    else fail('backend-badges-present', `lan=${lanBadge} cloud=${cloudBadge} rtsp=${rtspBadge}`);

    // ---------- Test 10: RTSP-Cam zeigt "RTSP"-Stream-Indikator
    const rtspCellText = await page.locator('#cameras-list table tbody tr').filter({ hasText: 'rtsp-1' }).first().innerText();
    if (rtspCellText.includes('RTSP')) pass('rtsp-stream-indicator');
    else fail('rtsp-stream-indicator', `Row text: ${rtspCellText.slice(0, 80)}`);

    // ---------- Test 11: Discovery-Button + Input
    const cidrInput = await page.locator('#cidr-input').inputValue();
    if (cidrInput.startsWith('192.168')) pass('discovery-cidr-default');
    else fail('discovery-cidr-default', `Got: ${cidrInput}`);

    // ---------- Test 12: Discovery scan (kleines /30 Range, schnell)
    await page.fill('#cidr-input', '198.51.100.0/30');
    await page.fill('#timeout-input', '0.1');
    await page.click('section:has-text("Discovery") button:has-text("Scan")');
    await page.waitForFunction(() => {
      const s = document.getElementById('discover-status')?.textContent || '';
      return s.includes('Host') || s.includes('Fehler');
    }, { timeout: 5000 });
    const discStatus = await page.locator('#discover-status').innerText();
    if (discStatus.includes('Host') && !discStatus.includes('Fehler')) pass('discovery-scan-runs');
    else fail('discovery-scan-runs', `Got: ${discStatus}`);

    // ---------- Test 13: Alarm-Listener Start (Mock-Route, sonst startet wirklich gegen Cams)
    await page.route('**/alarms/start', async (route) => {
      await new Promise(r => setTimeout(r, 100));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ started: true, task_count: 3, running: true, active_tasks: 3,
          started_at: Math.floor(Date.now() / 1000), per_cam: {}, events_total: 0,
          errors_total: 0, cloud_fallbacks: 0, mqtt_published: 0 }),
      });
    });
    await page.click('section:has-text("Alarm-Listener") button:has-text("Start")');
    await page.waitForFunction(() => {
      const t = document.getElementById('alarms-status-text')?.textContent || '';
      return t.includes('Running:');
    }, { timeout: 5000 });
    const alarmText = await page.locator('#alarms-status-text').innerText();
    if (alarmText.includes('Running: true')) pass('alarm-start-toggles-status');
    else fail('alarm-start-toggles-status', `Got: ${alarmText}`);

    // ---------- Screenshot final
    const shotPath = path.join(SHOTS_DIR, 'jarnex-ui-final.png');
    await page.screenshot({ path: shotPath, fullPage: true });
    pass(`screenshot-saved (${shotPath})`);

    // ---------- Test 14: Keine Console-Errors (whitelisted: 404 on missing favicon)
    const realErrors = consoleErrors.filter(e =>
      !e.includes('favicon') &&
      !e.includes('Failed to load resource: the server responded with a status of 404')
    );
    if (realErrors.length === 0) pass('no-console-errors');
    else fail('no-console-errors', `${realErrors.length} errors: ${realErrors.slice(0, 3).join(' | ')}`);

    await ctx.close();
  } finally {
    await browser.close();
  }

  const passed = results.filter(r => r.status === 'PASS').length;
  const total = results.length;
  console.log(`\n=== Summary: ${passed}/${total} passed ===`);
  if (passed < total) {
    process.exit(1);
  }
})().catch((e) => {
  console.error('FATAL:', e);
  process.exit(2);
});
