/* The Java assistant page follows the AI queue: the compiled Angular UI, the real API/worker, a slowly streaming
 * Ollama double (tests/assistant_follow_server.py).
 *   npm run build:ui && node tests/assistant_follow_browser.cjs
 * (playwright from the environment; FRM_BROWSER=<chromium> to choose the browser.)
 *
 * The reported case: the page kept the previous module and its state, so when the AI started right after a
 * migration the developer saw neither the live answer nor the result. Checked here:
 *   1. a module generated in this window opens in the assistant, its running trigger is selected and the live card
 *      streams the model's text; the result is shown when the run ends;
 *   2. a page opened (or reloaded) on an older module follows the module the AI works on;
 *   3. with following off the panel stays, the live card names the other module, and its button switches there.
 */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const delay = ms => new Promise(r => setTimeout(r, ms));
async function port() { const s = net.createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p; }
async function until(fn, description, tries = 300) {
  for (let i = 0; i < tries; i++) { if (await fn()) return; await delay(100); }
  throw new Error(description);
}

async function main() {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'frm-follow-'));
  const apiPort = await port(), aiPort = await port(), origin = 'http://localhost:' + apiPort, ai = 'http://127.0.0.1:' + aiPort;
  const server = spawn(process.env.FRM_PYTHON || 'python3', [path.join(root, 'tests/assistant_follow_server.py'), String(apiPort), String(aiPort), folder],
    {cwd: root, env: {...process.env, PYTHONPATH: [root, path.join(root, 'tests')].join(path.delimiter)}});
  let serverLog = ''; server.stderr.on('data', v => { serverLog += v; });
  let browser;
  const errors = [];
  const api = async (p, init) => (await fetch(origin + '/api' + p, init)).json();
  const post = (p, body) => api(p, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Frm-Client': 'local-ui'}, body: JSON.stringify(body)});
  try {
    await until(async () => { try { return (await fetch(origin + '/api/health')).ok; } catch { return false; } }, 'API did not start: ' + serverLog);
    browser = await chromium.launch({headless: true, ...(process.env.FRM_BROWSER ? {executablePath: process.env.FRM_BROWSER} : {}),
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    await context.addInitScript(() => localStorage.setItem('frm-options-v6', JSON.stringify({ai_mode: 'off', max_ai_calls: 0})));
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    const requested = []; page.on('request', r => requested.push(r.url()));
    const moduleShown = async () => (await page.getByRole('combobox', {name: 'Asszisztens modul', exact: true}).innerText()).trim();
    const live = page.getByTestId('ai-live');

    await page.goto(origin);
    // Served by the backend itself the UI connects on its own; an API dialog only when it asks.
    const connect = page.getByRole('dialog').getByRole('button', {name: 'Kapcsolódás', exact: true});
    if (await connect.waitFor({timeout: 2000}).then(() => true, () => false)) {
      await page.getByRole('dialog').locator('input').fill(origin + '/api'); await connect.click();
    }
    await page.getByText('Kapcsolódva', {exact: true}).waitFor();

    // The AI connection (the form needs it); the automatic start is off while the older module is generated.
    await page.getByLabel('Közös Ollama URL').fill(ai);
    await page.getByLabel('Közös Ollama modell').fill('browser-qwen:30b');
    await page.getByRole('button', {name: 'AI-kapcsolat mentése', exact: true}).click();
    await page.getByText('Az Ollama elérhető, a modell megtalálható. A közös AI-kapcsolat elmentve.', {exact: true}).waitFor();
    async function generate(module) {
      const before = (await api('/jobs')).jobs.length;
      await page.getByRole('tab', {name: 'Generálás', exact: true}).click();
      await page.locator('input[type=file]').first().setInputFiles(path.join(folder, 'lookup.xml'));
      await page.locator('input[formcontrolname=module]').fill(module);
      await page.locator('input[formcontrolname=AWU_AZON]').fill('1234');  // the company format (Lombok DTOs)
      await page.getByRole('button', {name: 'Generálás indítása', exact: true}).click();
      await until(async () => (await api('/jobs')).jobs.length > before, 'job was not created');
      const job = (await api('/jobs')).jobs.find(j => j.module === module);
      await until(async () => (await api('/jobs')).jobs.find(j => j.id === job.id).status === 'completed', 'generation failed: ' + serverLog, 600);
      for (let i = 0; i < 3 && await page.getByRole('dialog').count(); i++) { await page.keyboard.press('Escape'); await delay(200); }
      return job.id;
    }
    // No automatic insert: the trigger stays a task, so the AI can be started on it again below.
    await post('/assistant/settings', {auto_run: false, auto_apply: false});
    const first = await generate('elso');
    await page.getByRole('tab', {name: 'Java asszisztens', exact: true}).click();
    await until(async () => (await moduleShown()).startsWith('elso'), 'the assistant did not open the first module');
    assert.equal(await live.count(), 0, 'nothing runs for the first module');

    // 1. A new module: its trigger is queued at once, right after the migration.
    await post('/assistant/settings', {auto_run: true});
    const second = await generate('masodik');
    await page.getByRole('tab', {name: 'Java asszisztens', exact: true}).click();
    await until(async () => (await moduleShown()).startsWith('masodik'), 'the assistant kept the previous module: ' + await moduleShown());
    await live.waitFor({timeout: 15000});
    assert.equal(await page.getByTestId('ai-live-other').count(), 0, 'the run is this module\'s');
    await until(async () => /log1x|SERVICE/.test(await live.locator('pre').innerText().catch(() => '')), 'the live card did not stream the answer');
    await until(async () => /onCtrlRun/.test(await page.getByRole('combobox', {name: 'Asszisztens végpont', exact: true}).innerText()),
      'the running trigger was not selected');
    await live.waitFor({state: 'hidden', timeout: 30000});
    await until(async () => (await page.getByText('Átnézendő javaslat', {exact: true}).count()) > 0, 'the finished run is not listed');
    await until(async () => (await page.getByText('Korábbi javaslat', {exact: true}).count()) > 0
      && /onCtrlRun/.test(await page.getByRole('combobox', {name: 'Java-javaslat', exact: true}).innerText()), 'the result was not shown');

    // A new model call each time (an identical request is answered from the assistant's cache at once).
    let budget = 4096;
    const rerun = async job => { budget -= 64; await post('/assistant/settings', {num_predict: budget}); await post('/jobs/' + job + '/assistant/auto', {force: true}); };

    // 2. Reopened on the older module while the AI works on the new one: the page follows the AI.
    await rerun(second);
    await page.goto(origin + '/?view=assistant&job=' + first);
    await until(async () => (await moduleShown()).startsWith('masodik'), 'a page opened on the old module did not follow the AI: ' + await moduleShown());
    await live.waitFor({timeout: 15000});
    await live.waitFor({state: 'hidden', timeout: 30000});

    // 3. Following off: the panel stays on the module chosen, the live card names the other one and switches there.
    await page.getByRole('checkbox', {name: 'A futó AI-feladat követése', exact: true}).uncheck();
    await page.getByRole('combobox', {name: 'Asszisztens modul', exact: true}).click();
    await page.getByRole('option', {name: /^elso/}).click();
    await until(async () => (await moduleShown()).startsWith('elso'), 'the module choice did not apply');
    await rerun(second);
    const other = page.getByTestId('ai-live-other');
    await other.waitFor({timeout: 15000});
    assert.match(await other.innerText(), /masodik/);
    await delay(1500);
    assert((await moduleShown()).startsWith('elso'), 'with following off the panel must stay on the chosen module');
    await other.getByRole('button', {name: 'Átváltás erre a modulra', exact: true}).click();
    await until(async () => (await moduleShown()).startsWith('masodik'), 'the switch button did not open the other module');
    await until(async () => (await page.getByTestId('ai-live-other').count()) === 0 && (await live.count()) > 0, 'the live card did not move to this module');
    assert(await page.getByRole('checkbox', {name: 'A futó AI-feladat követése', exact: true}).isChecked(), 'the switch turns following on');

    await live.waitFor({state: 'hidden', timeout: 30000});

    // 4. A manual run, and "Modul frissítése" while it runs: the panel keeps following it and shows its result.
    await page.getByText('Memória és futtatás', {exact: true}).click();
    await page.getByRole('spinbutton').nth(1).fill('3500');  // Kimeneti keret: a new request, not the cached answer
    const before = (await api('/jobs/' + second + '/assistant')).proposals.length;
    const seenProposals = new Set((await api('/jobs/' + second + '/assistant')).proposals.map(item => item.id));
    const run = page.getByRole('button', {name: 'Trigger javásítása', exact: true});
    await run.click();
    await live.waitFor({timeout: 15000});
    await page.getByRole('button', {name: 'Modul frissítése', exact: true}).click();
    await live.waitFor({state: 'hidden', timeout: 30000});
    await until(async () => (await api('/jobs/' + second + '/assistant')).proposals.length > before, 'the manual run left no proposal');
    await until(async () => await run.isEnabled(), 'the manual run stayed "running" after a reload');
    const proposals = (await api('/jobs/' + second + '/assistant')).proposals;
    const latest = proposals.map(item => item.id).find(id => !seenProposals.has(id));
    assert(latest, 'no new proposal');
    await until(async () => requested.some(url => decodeURIComponent(url).includes('proposals/' + latest + '/')),
      'the panel did not show the new proposal of the manual run');

    const response = await fetch(origin + '/api/jobs/' + second + '/assistant/queue');
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await fetch(origin + '/')).headers.get('cache-control'), 'no-cache');
    assert.deepEqual(errors, []);
    console.log('assistant follow: ok');
  } finally {
    if (browser) await browser.close();
    server.kill();
    await fs.rm(folder, {recursive: true, force: true}).catch(() => {});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
