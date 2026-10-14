// Optional browser check: PLAYWRIGHT_MODULE=/path/to/playwright node tests/web-journal.cjs
// Uses the real static UI, a simulated API and the installed Chrome; no web server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let status = 200, source = { mode: 'system', error: '' }, instance = 'first', seq = 2;
    let entries = [
      { seq: 1, ts: 1792008000, host: 'ct107', unit: 'cron', text: 'Auftrag beendet', prio: 6 },
      { seq: 2, ts: 1792008001, host: 'wgz-pve-01', unit: 'kernel', text: 'USB verbunden', prio: 6 },
    ];
    let hold = false, release;
    await page.route('http://proximus.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/v1/journal') {
        if (hold) await new Promise(resolve => { release = resolve; });
        const after = Number(url.searchParams.get('after'));
        const same = !url.searchParams.get('instance') || url.searchParams.get('instance') === instance;
        await route.fulfill({ status, json: {
          seq, instance, source, host: 'ct107',
          lines: entries.filter(entry => !same || entry.seq > after),
        } }).catch(() => {});   // the disconnect check deliberately aborts
        return;
      }
      if (url.pathname === '/health') return route.fulfill({ json: { ok: true, ready: true } });
      if (url.pathname === '/v1/session') return route.fulfill({ json: { ok: false, password: true } });
      const relative = url.pathname === '/' ? 'web/index.html'
        : url.pathname === '/web/servo-skull.png' ? 'assets/display/servo-skull.png'
        : url.pathname.slice(1);
      const file = path.join(root, relative);
      if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404 });
      const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
        '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.png': 'image/png' }[path.extname(file)];
      return route.fulfill({ body: fs.readFileSync(file), contentType });
    });
    await page.goto('http://proximus.test/');
    await page.evaluate(async () => { Vox.connect(); await pollJournal(); });
    assert.equal(await page.locator('#journal [data-kind="log"]').count(), 2);
    assert.match(await page.locator('#journal').innerText(), /wgz-pve-01 ▸ kernel USB verbunden/);

    // An extended quiet period with all types of effects must preserve real logs.
    await page.evaluate(() => {
      for (let i = 0; i < 180; i++) {
        journalLine('Litanei', 'litany'); journalLine(interference(), 'noise'); Vox.onCrosstalk();
      }
    });
    assert.equal(await page.locator('#journal [data-kind="log"]').count(), 2);
    assert.equal(await page.locator('#journal [data-kind="ambient"]').count(), 3);
    assert(!/[01]{8}|0x[0-9A-F]+|FREMDKANAL \d/.test(await page.locator('#journal').innerText()));

    status = 401;
    await page.evaluate(async () => { await pollJournal(); await pollJournal(); });
    assert.equal(await page.locator('#journal [data-kind="status"]').count(), 1);
    assert.match(await page.locator('#journal [data-kind="status"]').innerText(), /Anmeldung abgelaufen/);
    assert.equal(await page.locator('#journal [data-kind="status"]').evaluate(e => getComputedStyle(e).order), '-1');
    status = 200;
    source.error = 'journalctl beendet';
    await page.evaluate(() => pollJournal());
    assert.match(await page.locator('#journal [data-kind="status"]').innerText(), /journalctl beendet/);
    source.error = '';
    await page.evaluate(() => pollJournal());
    assert.equal(await page.locator('#journal [data-kind="status"]').count(), 0);

    instance = 'second'; seq = 4;
    entries = [{ seq: 1, ts: 1792008002, host: 'pi-assistent', unit: 'pi-ptt', text: 'Pi sendet', prio: 6 }];
    await page.evaluate(() => pollJournal());
    assert.match(await page.locator('#journal').innerText(), /pi-assistent ▸ pi-ptt Pi sendet/);
    assert.equal(await page.evaluate(() => journal.instance), 'second');

    // A response from a disconnected channel cannot change the new channel's state.
    hold = true;
    await page.evaluate(() => { window.oldPoll = pollJournal(); });
    await page.waitForFunction(() => Boolean(journal.controller));
    for (let i = 0; !release && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert(release);
    await page.evaluate(() => stopJournal());
    release();
    await page.evaluate(async () => { await window.oldPoll; });
    assert.equal(await page.locator('#journal [data-kind="status"]').count(), 0);
    hold = false;

    hold = true; release = null;
    await page.evaluate(() => { window.slowPoll = pollJournal(); });
    for (let i = 0; !release && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert(release);
    await page.evaluate(async () => { await window.slowPoll; });
    assert.match(await page.locator('#journal [data-kind="status"]').innerText(), /unterbrochen/);
    assert.equal(await page.evaluate(() => journal.controller), null);
    release(); hold = false;
    await page.evaluate(() => pollJournal());
    assert.equal(await page.locator('#journal [data-kind="status"]').count(), 0);

    await page.evaluate(() => {
      document.body.dataset.connected = 'yes'; $('dial').hidden = true;
      for (let i = 0; i < 12; i++) journalLine(`Systemmeldung ${i}`);
    });
    await page.evaluate(() => { Vox.setEffects(false); document.body.dataset.effects = 'off'; });
    await page.waitForTimeout(800);
    await page.screenshot({ path: process.env.JOURNAL_SCREENSHOT || '/private/tmp/proximus-web-journal-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: '/private/tmp/proximus-web-journal-mobile.png' });
    assert.deepEqual(errors, []);
    console.log('Journal-Browserprüfung bestanden: Logs, Effekte, Anmeldung, Fehler, Neustart, Abbruch, Zeitlimit; Desktop und Mobil.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
